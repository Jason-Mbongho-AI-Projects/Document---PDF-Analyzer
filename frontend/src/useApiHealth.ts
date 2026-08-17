/**
 * Whether the backend is reachable.
 *
 * Not for its own sake — the point is to say which features still work. Much
 * of the app is a viewer over a document this tab has already downloaded, so
 * reading, searching, zooming and navigating carry on perfectly well while
 * the server is gone. Anything that changes the document does not.
 *
 * Without this the difference is invisible: the page looks normal until you
 * press a button, and then an error appears that reads like the feature is
 * broken rather than the connection.
 */
import { useCallback, useEffect, useState } from "react";
import { API_REACHED, API_UNREACHABLE } from "./api";

export type ApiState = "checking" | "online" | "offline";

/** Quiet while healthy; brisk while down, so recovery is noticed quickly. */
const HEALTHY_INTERVAL = 15_000;
const DOWN_INTERVAL = 5_000;

export function useApiHealth(): { state: ApiState; recheck: () => void } {
  const [state, setState] = useState<ApiState>("checking");

  const check = useCallback(async () => {
    try {
      // A timeout matters: a hung server is offline for our purposes, and
      // without one this promise can sit unresolved indefinitely.
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 4000);
      const response = await fetch("/health", {
        signal: controller.signal,
        cache: "no-store",
      });
      window.clearTimeout(timer);
      setState(response.ok ? "online" : "offline");
    } catch {
      setState("offline");
    }
  }, []);

  useEffect(() => {
    let timer: number;
    let cancelled = false;

    async function loop() {
      await check();
      if (cancelled) return;
      // Read the state through the setter so the interval always reflects the
      // latest result rather than the value captured when the loop started.
      setState((current) => {
        timer = window.setTimeout(
          loop, current === "online" ? HEALTHY_INTERVAL : DOWN_INTERVAL);
        return current;
      });
    }

    loop();

    // The browser telling us the network dropped is faster than any poll.
    const onOffline = () => setState("offline");
    const onOnline = () => check();

    // Faster still: a real request that just failed. Waiting for the next
    // poll leaves up to half a minute in which the app looks healthy and
    // every button fails, which is the worst of both states.
    const onUnreachable = () => setState("offline");
    const onReached = () => setState("online");

    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);
    window.addEventListener(API_UNREACHABLE, onUnreachable);
    window.addEventListener(API_REACHED, onReached);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
      window.removeEventListener(API_UNREACHABLE, onUnreachable);
      window.removeEventListener(API_REACHED, onReached);
    };
  }, [check]);

  return { state, recheck: check };
}
