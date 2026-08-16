/**
 * Autosave draft tests.
 *
 * The point of a draft is that it survives exactly as long as it should: long
 * enough to recover unsubmitted work, and not one moment past the work being
 * committed. Both halves are worth proving, along with the failure modes —
 * a full or disabled localStorage must never break the panel using it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import { clearDraft, readDraft, useAutosaveDraft } from "../useDraft";

const DOC = "doc-1";
const never = (_value: Record<string, string>) => false;

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** Render the hook with a mutable value, the way a form panel uses it. */
function harness(initial: Record<string, string>, isEmpty = never) {
  return renderHook(
    ({ value }) => useAutosaveDraft("form", DOC, value, isEmpty),
    { initialProps: { value: initial } },
  );
}

describe("useAutosaveDraft", () => {
  it("does not write anything on first render", () => {
    harness({ name: "already here" });
    act(() => void vi.advanceTimersByTime(2000));

    expect(readDraft("form", DOC)).toBeNull();
  });

  it("writes the value after the debounce settles", () => {
    const { rerender } = harness({});
    rerender({ value: { name: "Ada" } });

    // Not yet — the debounce is still running.
    act(() => void vi.advanceTimersByTime(300));
    expect(readDraft("form", DOC)).toBeNull();

    act(() => void vi.advanceTimersByTime(500));
    expect(readDraft("form", DOC)).toEqual({ name: "Ada" });
  });

  it("only writes once for a burst of typing", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const { rerender } = harness({});

    for (const name of ["A", "Ad", "Ada", "Ada L"]) {
      rerender({ value: { name } });
      act(() => void vi.advanceTimersByTime(100));
    }
    act(() => void vi.advanceTimersByTime(700));

    expect(setItem).toHaveBeenCalledTimes(1);
    expect(readDraft("form", DOC)).toEqual({ name: "Ada L" });
  });

  it("reports saving then settles back to clean", () => {
    const { result, rerender } = harness({});
    rerender({ value: { name: "Ada" } });

    expect(result.current.status).toBe("saving");
    act(() => void vi.advanceTimersByTime(700));
    expect(result.current.status).toBe("saved");
    act(() => void vi.advanceTimersByTime(2500));
    expect(result.current.status).toBe("clean");
  });

  it("removes the draft when the value becomes empty again", () => {
    const isEmpty = (v: Record<string, string>) =>
      Object.values(v).every((entry) => !entry);
    const { rerender } = harness({}, isEmpty);

    rerender({ value: { name: "Ada" } });
    act(() => void vi.advanceTimersByTime(700));
    expect(readDraft("form", DOC)).toEqual({ name: "Ada" });

    rerender({ value: { name: "" } });
    act(() => void vi.advanceTimersByTime(700));
    expect(readDraft("form", DOC)).toBeNull();
  });

  it("discard removes the stored draft", () => {
    const { result, rerender } = harness({});
    rerender({ value: { name: "Ada" } });
    act(() => void vi.advanceTimersByTime(700));

    act(() => result.current.discard());

    expect(readDraft("form", DOC)).toBeNull();
    expect(result.current.status).toBe("clean");
  });

  it("keeps one document's draft out of another's", () => {
    const { rerender } = harness({});
    rerender({ value: { name: "Ada" } });
    act(() => void vi.advanceTimersByTime(700));

    expect(readDraft("form", "another-doc")).toBeNull();
  });

  it("keeps separate scopes apart", () => {
    const { rerender } = harness({});
    rerender({ value: { name: "Ada" } });
    act(() => void vi.advanceTimersByTime(700));

    expect(readDraft("formbuilder", DOC)).toBeNull();
  });

  it("survives localStorage throwing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    const { result, rerender } = harness({});
    rerender({ value: { name: "Ada" } });

    // The failure must not surface as a claim that the draft was kept.
    expect(() => act(() => void vi.advanceTimersByTime(700))).not.toThrow();
    expect(result.current.status).toBe("clean");
  });
});

describe("readDraft", () => {
  it("returns null for corrupt json rather than throwing", () => {
    localStorage.setItem(`docintel.draft.form.${DOC}`, "{not json");
    expect(readDraft("form", DOC)).toBeNull();
  });

  it("ignores a draft written by an older format", () => {
    localStorage.setItem(
      `docintel.draft.form.${DOC}`,
      JSON.stringify({ version: 0, savedAt: Date.now(), value: { name: "Ada" } }),
    );
    expect(readDraft("form", DOC)).toBeNull();
  });

  it("discards a draft older than a week", () => {
    const eightDays = Date.now() - 8 * 24 * 60 * 60 * 1000;
    localStorage.setItem(
      `docintel.draft.form.${DOC}`,
      JSON.stringify({ version: 1, savedAt: eightDays, value: { name: "Ada" } }),
    );

    expect(readDraft("form", DOC)).toBeNull();
    // and does not leave the stale entry behind
    expect(localStorage.getItem(`docintel.draft.form.${DOC}`)).toBeNull();
  });

  it("clearDraft is safe when there is nothing stored", () => {
    expect(() => clearDraft("form", "missing")).not.toThrow();
  });
});
