/**
 * Autosave for work that only exists in the browser.
 *
 * Most of this app has nothing to autosave: every edit is an API call that
 * appends a new version server-side, and the status bar already reports that.
 * Two things were the exception — the values typed into a form before it is
 * submitted, and the fields placed in the form builder before the form is
 * created. Both lived in React state alone, so a refresh, a crashed tab or a
 * closed laptop lost them with no warning.
 *
 * They are saved to localStorage rather than the server deliberately. Neither
 * is a document change yet, so writing them server-side would either create
 * versions of work the user has not committed to, or need a drafts table that
 * outlives its usefulness. localStorage is scoped to the browser that typed
 * them, which is also where the expectation of recovery lives.
 *
 * Drafts carry the document id in the key, so opening a different document
 * never surfaces another document's half-finished input.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type DraftStatus = "clean" | "saving" | "saved";

/** Bumped if the stored shape ever changes, so old drafts are ignored. */
const VERSION = 1;
const PREFIX = "docintel.draft";

/** How long input must settle before it is written. */
const DEBOUNCE_MS = 600;

/** Drafts older than this are discarded on read. */
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

interface Stored<T> {
  version: number;
  savedAt: number;
  value: T;
}

function keyFor(scope: string, documentId: string) {
  return `${PREFIX}.${scope}.${documentId}`;
}

/** Read a draft, discarding anything stale, foreign or unparseable. */
export function readDraft<T>(scope: string, documentId: string): T | null {
  try {
    const raw = localStorage.getItem(keyFor(scope, documentId));
    if (!raw) return null;

    const parsed = JSON.parse(raw) as Stored<T>;
    if (parsed?.version !== VERSION) return null;
    if (!parsed.savedAt || Date.now() - parsed.savedAt > MAX_AGE_MS) {
      localStorage.removeItem(keyFor(scope, documentId));
      return null;
    }
    return parsed.value;
  } catch {
    // A quota-exceeded, disabled-storage or corrupt entry must never stop the
    // panel from rendering. No draft is a perfectly good outcome.
    return null;
  }
}

export function clearDraft(scope: string, documentId: string) {
  try {
    localStorage.removeItem(keyFor(scope, documentId));
  } catch {
    /* nothing to do — the draft simply outlives its welcome */
  }
}

/**
 * Debounced autosave of `value`.
 *
 * `isEmpty` decides what counts as nothing worth keeping; when it returns true
 * the stored draft is removed rather than written, so clearing a form clears
 * its draft too instead of leaving a ghost to be offered back later.
 */
export function useAutosaveDraft<T>(
  scope: string,
  documentId: string,
  value: T,
  isEmpty: (value: T) => boolean,
): { status: DraftStatus; discard: () => void } {
  const [status, setStatus] = useState<DraftStatus>("clean");

  // The first render is the restored (or initial) value, not an edit. Writing
  // it back would mark a freshly opened document as having a draft.
  const settled = useRef(false);
  const clearTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!settled.current) {
      settled.current = true;
      return;
    }

    setStatus("saving");
    const handle = window.setTimeout(() => {
      try {
        if (isEmpty(value)) {
          localStorage.removeItem(keyFor(scope, documentId));
        } else {
          const record: Stored<T> = { version: VERSION, savedAt: Date.now(), value };
          localStorage.setItem(keyFor(scope, documentId), JSON.stringify(record));
        }
        setStatus("saved");
        window.clearTimeout(clearTimer.current);
        clearTimer.current = window.setTimeout(() => setStatus("clean"), 2000);
      } catch {
        // Private browsing and full quotas both throw. Say nothing rather than
        // claim a save that did not happen.
        setStatus("clean");
      }
    }, DEBOUNCE_MS);

    return () => window.clearTimeout(handle);
    // isEmpty is intentionally excluded: callers pass an inline predicate, and
    // depending on it would reschedule the timer on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, documentId, value]);

  useEffect(() => () => window.clearTimeout(clearTimer.current), []);

  const discard = useCallback(() => {
    clearDraft(scope, documentId);
    setStatus("clean");
  }, [scope, documentId]);

  return { status, discard };
}
