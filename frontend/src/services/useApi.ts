import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export interface ApiState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Fetch `path` (skipped when null) and optionally re-poll every `pollMs` milliseconds. */
export function useApi<T>(path: string | null, pollMs?: number | false): ApiState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(path !== null);
  const current = useRef(path);
  current.current = path;

  const reload = useCallback(async () => {
    if (path === null) return;
    try {
      const result = await api<T>(path);
      if (current.current === path) {
        setData(result);
        setError(undefined);
      }
    } catch (err) {
      if (current.current === path) setError(err as Error);
    } finally {
      if (current.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(path !== null);
    void reload();
  }, [reload, path]);

  useEffect(() => {
    if (!pollMs || path === null) return;
    const id = window.setInterval(() => void reload(), pollMs);
    return () => window.clearInterval(id);
  }, [pollMs, reload, path]);

  return { data, error, loading, reload };
}
