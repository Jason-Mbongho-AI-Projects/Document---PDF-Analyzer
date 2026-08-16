/**
 * Contextual toolbar shown when text is selected in the viewer.
 *
 * Selection geometry comes from the browser's own range rectangles, converted
 * back into PDF points, so a highlight lands exactly on the glyphs the user
 * dragged over and stays aligned at any zoom.
 */
import { useEffect, useState } from "react";
import type { Rect } from "../api";

export interface Selection {
  text: string;
  page: number;
  rects: Rect[];          // PDF points, view origin
  anchor: { x: number; y: number };  // viewport pixels for toolbar placement
}

const COLOURS = ["#FFD54F", "#A5D6A7", "#90CAF9", "#F48FB1", "#CE93D8"];

interface Props {
  selection: Selection;
  onHighlight: (colour: string) => void;
  onUnderline: () => void;
  onStrike: () => void;
  onComment: () => void;
  onCopy: () => void;
  onRedact: () => void;
  onAsk: (mode: "explain" | "summarize" | "translate") => void;
}

export function SelectionToolbar({
  selection, onHighlight, onUnderline, onStrike, onComment, onCopy, onRedact, onAsk,
}: Props) {
  const [showColours, setShowColours] = useState(false);

  useEffect(() => setShowColours(false), [selection]);

  // Keep the toolbar on screen when the selection is near an edge.
  const left = Math.min(Math.max(selection.anchor.x - 150, 8), window.innerWidth - 320);
  const top = Math.max(selection.anchor.y - 48, 8);

  return (
    <div className="sel-toolbar" style={{ left, top }} onMouseDown={(e) => e.preventDefault()}>
      {showColours ? (
        <>
          {COLOURS.map((colour) => (
            <button
              key={colour}
              className="swatch"
              style={{ background: colour }}
              title={`Highlight ${colour}`}
              aria-label={`Highlight in ${colour}`}
              onClick={() => onHighlight(colour)}
            />
          ))}
          <button onClick={() => setShowColours(false)}>Back</button>
        </>
      ) : (
        <>
          <button onClick={onCopy} title="Copy (Ctrl+C)">Copy</button>
          <button onClick={() => setShowColours(true)}>Highlight</button>
          <button onClick={onUnderline}>Underline</button>
          <button onClick={onStrike}>Strike</button>
          <button onClick={onComment}>Comment</button>
          <span className="divider" />
          <button onClick={() => onAsk("explain")}>Explain</button>
          <button onClick={() => onAsk("summarize")}>Summarise</button>
          <button onClick={() => onAsk("translate")}>Translate</button>
          <span className="divider" />
          <button onClick={onRedact} style={{ color: "var(--bad)" }}>Redact</button>
        </>
      )}
    </div>
  );
}

/**
 * Read the current DOM selection and express it in PDF points.
 *
 * Returns null when the selection is empty or lies outside a page, so callers
 * can simply hide the toolbar.
 */
export function readSelection(scale: number): Selection | null {
  const domSelection = window.getSelection();
  if (!domSelection || domSelection.isCollapsed || !domSelection.rangeCount) return null;

  const text = domSelection.toString().trim();
  if (!text) return null;

  const range = domSelection.getRangeAt(0);
  const container =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as Element)
      : range.commonAncestorContainer.parentElement;

  const shell = container?.closest(".page-shell") as HTMLElement | null;
  if (!shell) return null;

  const page = Number(shell.dataset.page);
  if (!page) return null;

  const shellBox = shell.getBoundingClientRect();
  const rects: Rect[] = [];

  for (const box of Array.from(range.getClientRects())) {
    if (box.width < 1 || box.height < 1) continue;
    rects.push({
      x: (box.left - shellBox.left) / scale,
      y: (box.top - shellBox.top) / scale,
      width: box.width / scale,
      height: box.height / scale,
    });
  }
  if (!rects.length) return null;

  const last = range.getClientRects()[range.getClientRects().length - 1];
  return {
    text,
    page,
    rects,
    anchor: { x: last.left + last.width / 2, y: last.top },
  };
}
