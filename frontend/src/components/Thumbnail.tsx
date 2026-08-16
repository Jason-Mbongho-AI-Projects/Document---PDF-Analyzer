/**
 * Page thumbnail in the left rail.
 *
 * The render endpoint is authenticated, so the image is fetched with the
 * bearer token and shown from an object URL. Rendering is deferred until the
 * thumbnail scrolls into view and the object URL is revoked on unmount, so a
 * long document does not hold hundreds of decoded bitmaps in memory.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

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
  onDragOver?: () => void;
  onDrop?: () => void;
  onDragEnd?: () => void;
}

export function Thumbnail({
  documentId, page, active, selected, version, dragging, dropTarget,
  onClick, onDragStart, onDragOver, onDrop, onDragEnd,
}: Props) {
  const ref = useRef<HTMLButtonElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);

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

  return (
    <button
      ref={ref}
      className={`thumb ${active ? "active" : ""} ${selected ? "selected" : ""}` +
        `${dragging ? " dragging" : ""}${dropTarget ? " drop-target" : ""}`}
      onClick={onClick}
      draggable={!!onDragStart}
      onDragStart={(e) => {
        // Required for Firefox to start a drag at all.
        e.dataTransfer.setData("text/plain", String(page));
        e.dataTransfer.effectAllowed = "move";
        onDragStart?.();
      }}
      onDragOver={(e) => { e.preventDefault(); onDragOver?.(); }}
      onDrop={(e) => { e.preventDefault(); onDrop?.(); }}
      onDragEnd={onDragEnd}
      title="Click to jump · Ctrl/Shift-click to select · drag to reorder"
    >
      {url ? (
        <img src={url} alt={`Page ${page}`} />
      ) : (
        <div className="skeleton" aria-label={failed ? "Preview unavailable" : "Loading"} />
      )}
      <span className="num">{page}</span>
    </button>
  );
}
