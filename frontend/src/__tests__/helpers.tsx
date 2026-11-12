import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

export type Route = (url: string, init?: RequestInit) => unknown;

/** Mock fetch with a router function; records every call. */
export function mockFetch(route: Route) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const body = route(url, init);
      if (body instanceof Response) return body;
      return new Response(JSON.stringify(body ?? null), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return calls;
}

export function renderWithRouter(ui: ReactElement, path = "/") {
  return render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);
}
