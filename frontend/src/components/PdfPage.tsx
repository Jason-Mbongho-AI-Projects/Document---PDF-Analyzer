/**
 * One rendered PDF page: canvas + selectable text layer + overlays.
 *
 * Rendering is client-side via PDF.js so zooming stays sharp and the browser's
 * native selection works on real positioned glyphs — a server-rendered image
 * would give neither. The server's word geometry is still used for search
 * highlights and redaction, so both halves agree on where text sits.
 *
 * Pages render lazily: nothing is rasterised until it scrolls near the
 * viewport, which is what keeps a 500-page document usable.
 */
import { useEffect, useRef, useState } from "react";
import type { PDFPageProxy } from "pdfjs-dist";
import type { Annotation, Rect } from "../api";

export interface Overlay {
  key: string;
  rect: Rect;             // view coordinates, PDF points
  className: string;
  colour?: string;
  opacity?: number;
  title?: string;
  onClick?: () => void;
}

interface Props {
  page: PDFPageProxy;
  pageNumber: number;
  scale: number;
  overlays: Overlay[];
  snapshotMode: boolean;
  onSnapshot?: (pageNumber: number, rect: Rect) => void;
  onVisible?: (pageNumber: number) => void;
}

export function PdfPage({
  page, pageNumber, scale, overlays, snapshotMode, onSnapshot, onVisible,
}: Props) {
  const shellRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

  const viewport = page.getViewport({ scale });
  const width = Math.floor(viewport.width);
  const height = Math.floor(viewport.height);

  // Only rasterise pages that are close to the viewport.
  useEffect(() => {
    const element = shellRef.current;
    if (!element) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setNear(true);
            if (entry.intersectionRatio > 0.5) onVisible?.(pageNumber);
          }
        }
      },
      { root: null, rootMargin: "600px 0px", threshold: [0, 0.5] },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [pageNumber, onVisible]);

  useEffect(() => {
    if (!near) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    let cancelled = false;
    // Match the device pixel ratio so text stays crisp on high-DPI screens.
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const context = canvas.getContext("2d");
    if (!context) return;

    const task = page.render({
      canvasContext: context,
      viewport: page.getViewport({ scale: scale * ratio }),
    });

    task.promise.catch((error: unknown) => {
      if (!cancelled && (error as { name?: string })?.name !== "RenderingCancelledException") {
        console.error("page render failed", error);
      }
    });

    // The text layer is rebuilt whenever scale changes, since glyph positions
    // are absolute pixels.
    (async () => {
      const layer = textRef.current;
      if (!layer) return;
      layer.replaceChildren();

      try {
        const content = await page.getTextContent();
        if (cancelled) return;

        const fragment = document.createDocumentFragment();
        for (const item of content.items as any[]) {
          if (!item.str) continue;
          const tx = item.transform;
          const fontHeight = Math.hypot(tx[2], tx[3]) * scale;
          const span = document.createElement("span");
          span.textContent = item.str;
          span.style.left = `${tx[4] * scale}px`;
          span.style.top = `${viewport.height - (tx[5] * scale) - fontHeight}px`;
          span.style.fontSize = `${fontHeight}px`;
          span.style.fontFamily = item.fontName ?? "sans-serif";
          if (item.width) {
            span.style.transform = `scaleX(${(item.width * scale) / (fontHeight * item.str.length * 0.5 || 1)})`;
          }
          fragment.appendChild(span);
        }
        layer.appendChild(fragment);
      } catch {
        /* a page without extractable text simply has no selection layer */
      }
    })();

    return () => {
      cancelled = true;
      task.cancel();
    };
  }, [near, page, scale, width, height, viewport.height]);

  // --- snapshot drag ---------------------------------------------------
  //
  // Pointer events rather than mouse events, so a finger or stylus draws the
  // capture region exactly like a mouse does. The pointer is captured on down,
  // which keeps the drag alive if it leaves the page bounds mid-gesture, and
  // `touch-action: none` stops the browser from panning the document instead
  // of giving us the move events.

  function localPoint(event: React.PointerEvent) {
    const box = shellRef.current!.getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  function onPointerDown(event: React.PointerEvent) {
    if (!snapshotMode) return;
    // Ignore secondary mouse buttons and any second finger of a pinch.
    if (event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const p = localPoint(event);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  }

  function onPointerMove(event: React.PointerEvent) {
    if (!drag) return;
    const p = localPoint(event);
    setDrag({ ...drag, x1: p.x, y1: p.y });
  }

  function onPointerUp(event: React.PointerEvent) {
    if (!drag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const x = Math.min(drag.x0, drag.x1);
    const y = Math.min(drag.y0, drag.y1);
    const w = Math.abs(drag.x1 - drag.x0);
    const h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    if (w > 4 && h > 4) {
      // Convert screen pixels back to PDF points for the server.
      onSnapshot?.(pageNumber, {
        x: x / scale, y: y / scale, width: w / scale, height: h / scale,
      });
    }
  }

  return (
    <div
      ref={shellRef}
      className="page-shell"
      // Only claim the gesture while capturing a region; otherwise the page
      // must stay scrollable and text must stay selectable by touch.
      style={{ width, height, touchAction: snapshotMode ? "none" : undefined }}
      data-page={pageNumber}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => setDrag(null)}
    >
      <canvas ref={canvasRef} />

      <div className="annot-layer">
        {overlays.map((overlay) => (
          <div
            key={overlay.key}
            className={`annot ${overlay.className}`}
            title={overlay.title}
            onClick={overlay.onClick}
            style={{
              left: overlay.rect.x * scale,
              top: overlay.rect.y * scale,
              width: overlay.rect.width * scale,
              height: overlay.rect.height * scale,
              background: overlay.colour,
              opacity: overlay.opacity,
              pointerEvents: overlay.onClick ? "auto" : "none",
            }}
          />
        ))}
      </div>

      <div ref={textRef} className="text-layer" style={{ width, height }} />

      {snapshotMode && (
        <div className="snap-layer">
          {drag && (
            <div
              className="snap-rect"
              style={{
                left: Math.min(drag.x0, drag.x1),
                top: Math.min(drag.y0, drag.y1),
                width: Math.abs(drag.x1 - drag.x0),
                height: Math.abs(drag.y1 - drag.y0),
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** Convert a stored annotation into drawable overlay rectangles. */
export function annotationOverlays(
  annotation: Annotation,
  onClick?: () => void,
): Overlay[] {
  const rects: Rect[] = annotation.quads?.length
    ? annotation.quads
    : (annotation.rect as Rect)?.width
      ? [annotation.rect as Rect]
      : [];

  const isPin = annotation.kind === "note" || annotation.kind === "comment";

  return rects.map((rect, index) => ({
    key: `${annotation.id}-${index}`,
    rect: isPin ? { ...rect, width: 20, height: 20 } : rect,
    className: isPin ? "note-pin" : annotation.kind,
    colour: isPin ? undefined : annotation.colour,
    opacity: isPin ? 1 : annotation.opacity * 0.45,
    title: annotation.body ?? annotation.selected_text ?? annotation.kind,
    onClick,
  }));
}
