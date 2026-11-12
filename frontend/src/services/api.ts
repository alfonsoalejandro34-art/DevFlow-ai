const TOKEN_KEY = "devflow.token";
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode): keep the in-memory copy */
  }
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export const UNAUTHORIZED_EVENT = "devflow:unauthorized";

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const response = await fetch(path, {
    ...rest,
    headers: {
      Accept: "application/json",
      "X-DevFlow-Client": "web",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (response.status === 401) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  if (!response.ok) {
    let code = "http_error";
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { error?: { code: string; message: string } };
      if (body.error) ({ code, message } = body.error);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, code, message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function post<T>(path: string, json?: unknown): Promise<T> {
  return api<T>(path, { method: "POST", json: json ?? {} });
}
