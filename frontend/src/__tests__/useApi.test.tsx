import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApi } from "../services/useApi";
import { mockFetch } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

function Probe({ active }: { active: boolean }) {
  const { data } = useApi<{ n: number }>("/api/x", active && 50);
  return <div>value:{data?.n ?? "none"}</div>;
}

function Harness() {
  const [active, setActive] = useState(true);
  return (
    <>
      <button onClick={() => setActive(false)}>finish</button>
      <Probe active={active} />
    </>
  );
}

describe("useApi", () => {
  it("fetches the final state when polling stops", async () => {
    let n = 1;
    mockFetch(() => ({ n }));
    render(<Harness />);
    expect(await screen.findByText("value:1")).toBeInTheDocument();
    // The run finishes on the server, and the UI stops polling in the same moment.
    n = 2;
    screen.getByText("finish").click();
    await waitFor(() => expect(screen.getByText("value:2")).toBeInTheDocument());
  });
});
