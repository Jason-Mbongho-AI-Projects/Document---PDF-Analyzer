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

/** Tools that draw a mark rather than select text. */
export type DrawTool = "shape" | "arrow" | "draw" | "textbox" | "note";

/** A path-based mark: arrows and freehand, which no rectangle can express. */
export interface Stroke {
  key: string;
  kind: "arrow" | "drawing";
  points: { x: number; y: number }[];     // view coordinates, PDF points
  colour: string;
  opacity?: number;
  onClick?: () => void;
}

export interface DrawResult {
  rect?: Rect;
  points?: { x: number; y: number }[];
}

interface Props {
  page: PDFPageProxy;
  pageNumber: number;
  scale: number;
  overlays: Overlay[];
  strokes?: Stroke[];
  snapshotMode: boolean;
  drawTool?: DrawTool | null;
  onSnapshot?: (pageNumber: number, rect: Rect) => void;
  onDraw?: (pageNumber: number, tool: DrawTool, result: DrawResult) => void;
  onVisible?: (pageNumber: number) => void;
}

export function PdfPage({
  page, pageNumber, scale, overlays, strokes = [], snapshotMode,
  drawTool = null, onSnapshot, onDraw, onVisible,
}: Props) {
  const shellRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  // Freehand collects a path rather than a box.
  const [inkPath, setInkPath] = useState<{ x: number; y: number }[]>([]);

  // Any tool that paints takes the pointer; text selection keeps it otherwise.
  const capturing = snapshotMode || drawTool !== null;

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
    if (!capturing) return;
    // Ignore secondary mouse buttons and any second finger of a pinch.
    if (event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const p = localPoint(event);

    // A note marks a point, so one tap is the whole gesture.
    if (drawTool === "note") {
      onDraw?.(pageNumber, "note", {
        rect: { x: p.x / scale, y: p.y / scale, width: 0, height: 0 },
      });
      return;
    }

    if (drawTool === "draw") setInkPath([p]);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
  }

  function onPointerMove(event: React.PointerEvent) {
    if (!drag) return;
    const p = localPoint(event);
    setDrag({ ...drag, x1: p.x, y1: p.y });

    if (drawTool === "draw") {
      // Thin the path: a point every few pixels captures the shape without
      // storing hundreds of positions per stroke.
      setInkPath((path) => {
        const last = path[path.length - 1];
        if (last && Math.hypot(p.x - last.x, p.y - last.y) < 2.5) return path;
        return [...path, p];
      });
    }
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
    const from = { x: drag.x0, y: drag.y0 };
    const to = { x: drag.x1, y: drag.y1 };
    const path = inkPath;

    setDrag(null);
    setInkPath([]);

    if (drawTool === "draw") {
      if (path.length >= 2) {
        onDraw?.(pageNumber, "draw", {
          points: path.map((p) => ({ x: p.x / scale, y: p.y / scale })),
        });
      }
      return;
    }

    if (drawTool === "arrow") {
      // Direction matters, so the two ends are kept rather than a bounding box.
      if (Math.hypot(to.x - from.x, to.y - from.y) > 8) {
        onDraw?.(pageNumber, "arrow", {
          points: [
            { x: from.x / scale, y: from.y / scale },
            { x: to.x / scale, y: to.y / scale },
          ],
        });
      }
      return;
    }

    if (w <= 4 || h <= 4) return;      // a stray click, not a drag
    const rect: Rect = {
      x: x / scale, y: y / scale, width: w / scale, height: h / scale,
    };

    if (drawTool) {
      onDraw?.(pageNumber, drawTool, { rect });
    } else if (snapshotMode) {
      onSnapshot?.(pageNumber, rect);
    }
  }

  return (
    <div
      ref={shellRef}
      className="page-shell"
      // Only claim the gesture while capturing a region; otherwise the page
      // must stay scrollable and text must stay selectable by touch.
      style={{ width, height, touchAction: capturing ? "none" : undefined,
               cursor: drawTool ? "crosshair" : undefined }}
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

      {/* Arrows and freehand are paths, which no positioned div can express.
          Drawn in SVG over the page, and previewed live while dragging. */}
      {(strokes.length > 0 || drawTool === "arrow" || drawTool === "draw") && (
        <svg className="ink-layer" width={width} height={height}
             viewBox={`0 0 ${width} ${height}`}>
          {strokes.map((stroke) => {
            const points = stroke.points
              .map((p) => `${p.x * scale},${p.y * scale}`).join(" ");
            return stroke.kind === "arrow" ? (
              <g key={stroke.key} stroke={stroke.colour} fill={stroke.colour}
                 opacity={stroke.opacity ?? 1}
                 style={{ pointerEvents: stroke.onClick ? "auto" : "none",
                          cursor: stroke.onClick ? "pointer" : undefined }}
                 onClick={stroke.onClick}>
                <polyline points={points} strokeWidth={1.8} fill="none" />
                {arrowHead(stroke.points, scale)}
              </g>
            ) : (
              <polyline key={stroke.key} points={points}
                        stroke={stroke.colour} strokeWidth={2} fill="none"
                        strokeLinecap="round" strokeLinejoin="round"
                        opacity={stroke.opacity ?? 1}
                        style={{ pointerEvents: stroke.onClick ? "auto" : "none",
                                 cursor: stroke.onClick ? "pointer" : undefined }}
                        onClick={stroke.onClick} />
            );
          })}

          {drag && drawTool === "arrow" && (
            <line x1={drag.x0} y1={drag.y0} x2={drag.x1} y2={drag.y1}
                  stroke="#1D4ED8" strokeWidth={1.8} strokeDasharray="4 3" />
          )}
          {drawTool === "draw" && inkPath.length > 1 && (
            <polyline
              points={inkPath.map((p) => `${p.x},${p.y}`).join(" ")}
              stroke="#BE123C" strokeWidth={2} fill="none"
              strokeLinecap="round" strokeLinejoin="round" />
          )}
        </svg>
      )}

      {/* A rectangle tool previews its box the same way snapshot does. */}
      {drag && (drawTool === "shape" || drawTool === "textbox") && (
        <div className="snap-layer">
          <div className="snap-rect" style={{
            left: Math.min(drag.x0, drag.x1),
            top: Math.min(drag.y0, drag.y1),
            width: Math.abs(drag.x1 - drag.x0),
            height: Math.abs(drag.y1 - drag.y0),
          }} />
        </div>
      )}

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

/** The filled triangle at an arrow's far end. */
function arrowHead(points: { x: number; y: number }[], scale: number) {
  if (points.length < 2) return null;
  const from = points[points.length - 2];
  const to = points[points.length - 1];
  const x1 = from.x * scale, y1 = from.y * scale;
  const x2 = to.x * scale, y2 = to.y * scale;

  const angle = Math.atan2(y2 - y1, x2 - x1);
  const length = 10;
  const spread = 0.42;                    // radians, ~24 degrees
  const left = [x2 - length * Math.cos(angle - spread),
                y2 - length * Math.sin(angle - spread)];
  const right = [x2 - length * Math.cos(angle + spread),
                 y2 - length * Math.sin(angle + spread)];

  return <polygon points={`${x2},${y2} ${left} ${right}`} stroke="none" />;
}

/** Split stored annotations into rectangles and paths.
 *
 * Arrows and freehand carry a run of points; everything else is areas. They
 * render through different layers, so they are separated here rather than in
 * every caller.
 */
export function annotationStrokes(
  annotation: Annotation,
  onClick?: () => void,
): Stroke[] {
  if (annotation.kind !== "arrow" && annotation.kind !== "drawing") return [];

  const points = (annotation.quads ?? [])
    .filter((q) => q && typeof q.x === "number" && typeof q.y === "number")
    .map((q) => ({ x: q.x, y: q.y }));
  if (points.length < 2) return [];

  return [{
    key: annotation.id,
    kind: annotation.kind,
    points,
    colour: annotation.colour,
    opacity: annotation.opacity,
    onClick,
  }];
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

  // Paths render in the SVG layer instead; returning rectangles for them
  // would draw a stray box where the stroke is.
  if (annotation.kind === "arrow" || annotation.kind === "drawing") return [];

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
