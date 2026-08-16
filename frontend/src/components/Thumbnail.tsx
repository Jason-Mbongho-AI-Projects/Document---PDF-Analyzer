/**
 * Page thumbnail in the left rail.
 *
 * The render endpoint is authenticated, so the image is fetched with the
 * bearer token and shown from an object URL. Rendering is deferred until the
 * thumbnail scrolls into view and the object URL is revoked on unmount, so a
 * long document does not hold hundreds of decoded bitmaps in memory.
 *
 * Reordering uses pointer events, not HTML5 drag-and-drop. HTML5 drag never
 * fires on touch devices, which left page reorder working on a desktop and
 * silently dead on a tablet. Pointer events cover mouse, touch and stylus with
 * one code path, at the cost of having to tell a drag apart from a tap
 * ourselves — hence the movement threshold below.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/** Pixels of travel before a press becomes a drag rather than a tap. */
const DRAG_THRESHOLD = 6;

interface Props {
  documentId: string;
  page: number;
  active: boolean;
  selected: boolean;
  version: number;      // bump to force a re-fetch after an edit
  dragging?: boolean;
  dropTarget?: boolean;
  onClick: (event: React.MouseEvent) => void;
  onDragStart?: () => void;
  /** Fired with the page currently under the pointer, or null if none. */
  onDragOverPage?: (page: number | null) => void;
  onDrop?: () => void;
  onDragEnd?: () => void;
}

/** Which thumbnail sits under this point, if any. */
function pageUnder(x: number, y: number): number | null {
  for (const element of document.elementsFromPoint(x, y)) {
    const holder = (element as HTMLElement).closest?.("[data-thumb-page]");
    if (holder) {
      const value = Number(holder.getAttribute("data-thumb-page"));
      if (Number.isFinite(value)) return value;
    }
  }
  return null;
}

export function Thumbnail({
  documentId, page, active, selected, version, dragging, dropTarget,
  onClick, onDragStart, onDragOverPage, onDrop, onDragEnd,
}: Props) {
  const ref = useRef<HTMLButtonElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);

  // Gesture bookkeeping. Kept in a ref so the move handler never works from a
  // stale render, and so a drag in progress survives re-renders of the rail.
  const gesture = useRef<{ startX: number; startY: number; active: boolean } | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && setVisible(true)),
      { rootMargin: "300px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let objectUrl: string | null = null;
    let cancelled = false;

    setFailed(false);
    api
      .renderPage(documentId, page)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => !cancelled && setFailed(true));

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, page, visible, version]);

  const reorderable = !!onDragStart;

  function onPointerDown(event: React.PointerEvent<HTMLButtonElement>) {
    if (!reorderable || event.button !== 0 || !event.isPrimary) return;
    // Capture so we keep receiving moves once the finger leaves this thumbnail.
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { startX: event.clientX, startY: event.clientY, active: false };
  }

  function onPointerMove(event: React.PointerEvent<HTMLButtonElement>) {
    const g = gesture.current;
    if (!g) return;

    if (!g.active) {
      const moved = Math.hypot(event.clientX - g.startX, event.clientY - g.startY);
      if (moved < DRAG_THRESHOLD) return;   // still a tap, not yet a drag
      g.active = true;
      onDragStart?.();
    }

    onDragOverPage?.(pageUnder(event.clientX, event.clientY));
  }

  function onPointerUp(event: React.PointerEvent<HTMLButtonElement>) {
    const g = gesture.current;
    gesture.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (!g) return;

    if (!g.active) {
      // Never moved far enough to be a drag — treat it as a plain click. The
      // button's own onClick does not fire reliably after pointer capture, so
      // the click is dispatched here instead.
      onClick(event as unknown as React.MouseEvent);
      return;
    }

    const target = pageUnder(event.clientX, event.clientY);
    if (target !== null) onDragOverPage?.(target);
    onDrop?.();
    onDragEnd?.();
  }

  function onPointerCancel() {
    const wasDragging = gesture.current?.active;
    gesture.current = null;
    if (wasDragging) onDragEnd?.();
  }

  return (
    <button
      ref={ref}
      data-thumb-page={page}
      className={`thumb ${active ? "active" : ""} ${selected ? "selected" : ""}` +
        `${dragging ? " dragging" : ""}${dropTarget ? " drop-target" : ""}`}
      // Claim vertical gestures only where reordering is possible, so the rail
      // still scrolls by touch everywhere else.
      style={reorderable ? { touchAction: "none" } : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      // Non-reorderable thumbnails keep the ordinary click path.
      onClick={reorderable ? undefined : onClick}
      title={reorderable
        ? "Tap to jump · Ctrl/Shift-click to select · drag to reorder"
        : "Tap to jump"}
    >
      {url ? (
        <img src={url} alt={`Page ${page}`} draggable={false} />
      ) : (
        <div className="skeleton" aria-label={failed ? "Preview unavailable" : "Loading"} />
      )}
      <span className="num">{page}</span>
    </button>
  );
}
