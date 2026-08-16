"""
Writing annotations into the PDF itself.

Annotations are stored in the database, not in the file. That is the right
default — marking up a document should not rewrite it, and it lets several
people comment without fighting over bytes — but it has a sharp edge: the
document you download has none of your comments in it, and nothing says so.

This module closes that gap. It paints the stored annotations onto the pages
and returns new bytes, so a copy can be produced for sending to someone who
will never see this application.

Flattening is deliberately one-way. The marks become part of the page, exactly
as they appear on screen, and cannot be edited afterwards. The database copy
remains the editable one.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from reportlab.lib.colors import HexColor

from docintel.pdf.engine import PDFEngineError
from docintel.pdf.operations import PyPdfEngine

# Note pins are drawn at a fixed size: they mark a point, and scaling them
# with a zero-sized rect would make them invisible.
PIN = 14.0
DEFAULT_COLOUR = "#FFD54F"


def _colour(value: str):
    try:
        return HexColor(value)
    except Exception:
        return HexColor(DEFAULT_COLOUR)


def _rects(annotation: dict) -> List[dict]:
    """Every rectangle an annotation covers, in view coordinates."""
    quads = annotation.get("quads") or []
    if quads:
        return [q for q in quads if q.get("width") and q.get("height")]
    rect = annotation.get("rect") or {}
    if rect.get("width") and rect.get("height"):
        return [rect]
    if rect:
        return [rect]      # a point, for notes
    return []


def _points(annotation: dict) -> List[dict]:
    """The path of an arrow or a freehand stroke.

    These are stored as a run of positions rather than areas, so they must not
    go through the rectangle filter — a point legitimately has no width, and
    filtering on size would discard the whole stroke.
    """
    quads = annotation.get("quads") or []
    points = [q for q in quads if "x" in q and "y" in q]
    if points:
        return points

    # An arrow with only a bounding box is drawn along its diagonal, which is
    # how the earlier format recorded it.
    rect = annotation.get("rect") or {}
    if rect.get("width") is not None and rect.get("height") is not None:
        return [
            {"x": rect.get("x", 0), "y": rect.get("y", 0)},
            {"x": rect.get("x", 0) + rect.get("width", 0),
             "y": rect.get("y", 0) + rect.get("height", 0)},
        ]
    return []


def flatten(data: bytes, annotations: Sequence[dict],
            *, include_notes: bool = True) -> bytes:
    """Paint annotations onto the page and return the new document.

    `annotations` are dictionaries as the API returns them: kind, page, rect,
    quads, colour, opacity, body.
    """
    if not annotations:
        raise PDFEngineError("There are no annotations to write into the file.")

    by_page: Dict[int, List[dict]] = {}
    for annotation in annotations:
        page = int(annotation.get("page") or 0)
        if page >= 1:
            by_page.setdefault(page, []).append(annotation)

    if not by_page:
        raise PDFEngineError("None of the annotations name a valid page.")

    engine = PyPdfEngine()

    def make_overlay(index: int, width: float, height: float):
        def draw(pdf):
            for annotation in by_page[index]:
                _draw_one(pdf, annotation, height, include_notes)
        return engine._overlay(width, height, draw)

    return engine._compose(
        data,
        should_stamp=lambda index: index in by_page,
        make_overlay=make_overlay,
        # Highlights must sit under the text to remain readable; everything
        # else is drawn over it. Two passes would be tidier, but a single
        # over-pass with transparency reads correctly for all of them.
        over=True,
    )


def _draw_one(pdf, annotation: dict, page_height: float,
              include_notes: bool) -> None:
    kind = str(annotation.get("kind") or "").lower()
    colour = _colour(str(annotation.get("colour") or DEFAULT_COLOUR))
    opacity = float(annotation.get("opacity") or 1.0)

    # Arrows and freehand strokes are runs of points, not areas. Asking for
    # rectangles first would filter them out — a point has no width — and the
    # annotation would silently vanish.
    if kind in ("arrow", "drawing"):
        rects = []
    else:
        rects = _rects(annotation)
        if not rects:
            return

    pdf.saveState()
    try:
        if kind == "highlight":
            # Multiply keeps the text legible through the colour instead of
            # painting a solid block over it.
            pdf.setFillColor(colour)
            pdf.setFillAlpha(min(max(opacity * 0.45, 0.05), 0.6))
            for rect in rects:
                pdf.rect(rect["x"], _bottom(rect, page_height),
                         rect["width"], rect["height"], stroke=0, fill=1)

        elif kind in ("underline", "strikethrough"):
            pdf.setStrokeColor(colour)
            pdf.setStrokeAlpha(min(opacity, 1.0))
            pdf.setLineWidth(max(rects[0]["height"] * 0.07, 0.6))
            for rect in rects:
                bottom = _bottom(rect, page_height)
                y = (bottom + rect["height"] * 0.5) if kind == "strikethrough" \
                    else (bottom + rect["height"] * 0.08)
                pdf.line(rect["x"], y, rect["x"] + rect["width"], y)

        elif kind in ("shape", "textbox"):
            pdf.setStrokeColor(colour)
            pdf.setStrokeAlpha(min(opacity, 1.0))
            pdf.setLineWidth(1.2)
            for rect in rects:
                pdf.rect(rect["x"], _bottom(rect, page_height),
                         rect["width"], rect["height"], stroke=1, fill=0)
            if kind == "textbox" and annotation.get("body"):
                _label(pdf, rects[0], page_height, str(annotation["body"]), colour)

        elif kind == "arrow":
            path = _points(annotation)
            if len(path) < 2:
                return
            pdf.setStrokeColor(colour)
            pdf.setFillColor(colour)
            pdf.setStrokeAlpha(min(opacity, 1.0))
            pdf.setLineWidth(1.6)
            start, end = path[0], path[-1]
            x1, y1 = start["x"], page_height - start["y"]
            x2, y2 = end["x"], page_height - end["y"]
            pdf.line(x1, y1, x2, y2)
            _arrow_head(pdf, x1, y1, x2, y2)

        elif kind == "drawing":
            path = _points(annotation)
            if len(path) < 2:
                return
            pdf.setStrokeColor(colour)
            pdf.setStrokeAlpha(min(opacity, 1.0))
            pdf.setLineWidth(1.8)
            pdf.setLineCap(1)               # round, so joins do not look chipped
            track = pdf.beginPath()
            track.moveTo(path[0]["x"], page_height - path[0]["y"])
            for point in path[1:]:
                track.lineTo(point["x"], page_height - point["y"])
            pdf.drawPath(track, stroke=1, fill=0)

        elif kind in ("note", "comment", "stamp") and include_notes:
            rect = rects[0]
            bottom = _bottom(rect, page_height)
            pdf.setFillColor(colour)
            pdf.setFillAlpha(min(opacity, 1.0))
            pdf.circle(rect["x"] + PIN / 2, bottom + PIN / 2, PIN / 2,
                       stroke=0, fill=1)
            if annotation.get("body"):
                _label(pdf, {"x": rect["x"] + PIN + 3, "y": rect.get("y", 0),
                             "width": 220, "height": PIN},
                       page_height, str(annotation["body"]), colour,
                       bottom_override=bottom)
    finally:
        pdf.restoreState()


def _arrow_head(pdf, x1: float, y1: float, x2: float, y2: float,
                length: float = 9.0) -> None:
    """A filled head at the far end, so the arrow points at something."""
    import math

    angle = math.atan2(y2 - y1, x2 - x1)
    spread = math.radians(24)
    left = (x2 - length * math.cos(angle - spread),
            y2 - length * math.sin(angle - spread))
    right = (x2 - length * math.cos(angle + spread),
             y2 - length * math.sin(angle + spread))

    head = pdf.beginPath()
    head.moveTo(x2, y2)
    head.lineTo(*left)
    head.lineTo(*right)
    head.close()
    pdf.drawPath(head, stroke=0, fill=1)


def _bottom(rect: dict, page_height: float) -> float:
    """Rectangles arrive in view coordinates; PDF measures from the bottom."""
    return page_height - rect.get("y", 0) - rect.get("height", 0)


def _label(pdf, rect: dict, page_height: float, text: str, colour,
           bottom_override: float | None = None) -> None:
    """Draw a short piece of comment text, trimmed to one line."""
    bottom = bottom_override if bottom_override is not None \
        else _bottom(rect, page_height)
    trimmed = " ".join(text.split())
    if len(trimmed) > 90:
        trimmed = trimmed[:87] + "…"

    pdf.setFillColor(colour)
    pdf.setFillAlpha(1.0)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(rect["x"] + 2, bottom + 3, trimmed)
