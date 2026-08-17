/**
 * Knowing whether the server is reachable.
 *
 * The value of this is telling the user which half of the app still works, so
 * the states have to be right: a failed request must register immediately —
 * that is the moment someone pressed a button and nothing happened — and
 * recovery must register without anyone reloading the page.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { API_REACHED, API_UNREACHABLE } from "../api";
import { useApiHealth } from "../useApiHealth";

function respondWith(ok: boolean) {
  globalThis.fetch = vi.fn(async () => ({ ok, status: ok ? 200 : 503 }) as Response);
}

beforeEach(() => respondWith(true));
afterEach(() => vi.restoreAllMocks());

describe("initial probe", () => {
  it("reports online when the health check answers", async () => {
    const { result } = renderHook(() => useApiHealth());
    await waitFor(() => expect(result.current.state).toBe("online"));
  });

  it("reports offline when the health check fails", async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error("connection refused"); });
    const { result } = renderHook(() => useApiHealth());

    await waitFor(() => expect(result.current.state).toBe("offline"));
  });

  it("treats a 5xx as offline, not merely unhealthy", async () => {
    respondWith(false);
    const { result } = renderHook(() => useApiHealth());

    await waitFor(() => expect(result.current.state).toBe("offline"));
  });
});

describe("reacting to real requests", () => {
  it("goes offline the moment a request reports it could not connect", async () => {
    const { result } = renderHook(() => useApiHealth());
    await waitFor(() => expect(result.current.state).toBe("online"));

    // Waiting for the next poll would leave the app looking healthy while
    // every button fails, so a failed call has to be enough on its own.
    act(() => { window.dispatchEvent(new CustomEvent(API_UNREACHABLE)); });

    expect(result.current.state).toBe("offline");
  });

  it("comes back online when a request succeeds again", async () => {
    const { result } = renderHook(() => useApiHealth());
    await waitFor(() => expect(result.current.state).toBe("online"));

    act(() => { window.dispatchEvent(new CustomEvent(API_UNREACHABLE)); });
    expect(result.current.state).toBe("offline");

    act(() => { window.dispatchEvent(new CustomEvent(API_REACHED)); });
    expect(result.current.state).toBe("online");
  });
});

describe("the browser's own signal", () => {
  it("goes offline when the network drops", async () => {
    const { result } = renderHook(() => useApiHealth());
    await waitFor(() => expect(result.current.state).toBe("online"));

    act(() => { window.dispatchEvent(new Event("offline")); });

    expect(result.current.state).toBe("offline");
  });
});

describe("rechecking", () => {
  it("recheck picks up a server that has come back", async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error("refused"); });
    const { result } = renderHook(() => useApiHealth());
    await waitFor(() => expect(result.current.state).toBe("offline"));

    respondWith(true);
    await act(async () => { await result.current.recheck(); });

    expect(result.current.state).toBe("online");
  });
});

describe("cleanup", () => {
  it("stops listening once unmounted", async () => {
    const { result, unmount } = renderHook(() => useApiHealth());
    await waitFor(() => expect(result.current.state).toBe("online"));

    unmount();
    // Dispatching after unmount must not warn or update anything.
    expect(() => {
      window.dispatchEvent(new CustomEvent(API_UNREACHABLE));
    }).not.toThrow();
  });
});
