"""
Editing the text of a PDF: replace it, delete it, add more.

What this can and cannot do, stated up front, because the difference matters
and no amount of interface hides it.

A PDF does not store editable text. It stores instructions to paint glyphs at
coordinates, using fonts that are usually embedded and usually subsetted --
cut down to only the glyphs the document happens to use. There is no "the
paragraph"; there is a sequence of show-text operators. Two consequences
follow:

  * Removing text is exact. The glyphs are deleted from the content stream, so
    the characters are genuinely gone, not covered over.

  * Adding text cannot reuse the original font. A subsetted font frequently
    does not contain the glyphs the replacement needs, and pypdf cannot extend
    a font program. Replacement text is therefore drawn in a standard font,
    matched for size, colour and position. Against Helvetica or Times the
    result is usually indistinguishable; against a distinctive embedded
    typeface it will not match, and the API says which font it used so the
    caller can tell the user rather than let them discover it.

  * Text does not reflow. Replacing a long run with a short one leaves the gap;
    replacing a short run with a long one can overrun what follows. The caller
    is told the estimated width so it can warn.

Every edit is verified by re-extracting the output: removed text must be gone
and added text must be present, or the operation fails rather than returning a
document that only looks edited.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pikepdf
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics

from docintel.pdf.engine import PDFEngineError
from docintel.pdf.redact import TEXT_OPERATORS
from docintel.pdf.text import extract

# The standard 14 fonts, present in every PDF reader without embedding.
FONTS = {
    "Helvetica": {"": "Helvetica", "b": "Helvetica-Bold",
                  "i": "Helvetica-Oblique", "bi": "Helvetica-BoldOblique"},
    "Times": {"": "Times-Roman", "b": "Times-Bold",
              "i": "Times-Italic", "bi": "Times-BoldItalic"},
    "Courier": {"": "Courier", "b": "Courier-Bold",
                "i": "Courier-Oblique", "bi": "Courier-BoldOblique"},
}

DEFAULT_SIZE = 11.0


@dataclass
class Style:
    font: str = "Helvetica"
    size: Optional[float] = None      # None means "match what was there"
    colour: str = "#000000"
    bold: bool = False
    italic: bool = False

    def resolved_font(self) -> str:
        family = FONTS.get(self.font) or FONTS["Helvetica"]
        key = ("b" if self.bold else "") + ("i" if self.italic else "")
        return family.get(key, family[""])


@dataclass
class Edit:
    """Replace `find` with `replace` on `page`. Empty `replace` deletes."""
    page: int
    find: str
    replace: str = ""
    style: Style = field(default_factory=Style)
    # Which occurrence to act on, 0-based; None means every occurrence.
    occurrence: Optional[int] = None
    # Shrink a too-wide replacement so it occupies the original's width.
    # On by default: PDF text does not reflow, so the alternative is text that
    # visibly runs into whatever follows it on the line.
    fit_to_width: bool = True


# A replacement is never shrunk below this fraction of the original size —
# past it the text is too small to read, and saying so beats producing it.
MIN_FIT_RATIO = 0.6


@dataclass
class Occurrence:
    page: int
    text: str
    x: float
    y: float               # baseline, PDF coordinates
    width: float
    height: float

    def as_dict(self) -> dict:
        return {"page": self.page, "text": self.text,
                "x": round(self.x, 2), "y": round(self.y, 2),
                "width": round(self.width, 2), "height": round(self.height, 2)}


# Punctuation that commonly clings to a word and is not part of what someone
# means when they select or type the phrase.
_EDGE = ".,;:!?()[]{}\"'“”‘’…"


def _window_matches(window, wanted: Sequence[str]) -> bool:
    """Match a run of words against the phrase, ignoring edge punctuation.

    Selecting "Amount due" from a line reading "Amount due:" is the ordinary
    case, and a matcher that insists on the colon simply never finds anything.
    Inner words must match exactly; only the outer edges are forgiving.
    """
    if len(window) != len(wanted):
        return False

    for index, (word, target) in enumerate(zip(window, wanted)):
        actual = word.text.lower()
        expected = target.lower()
        if actual == expected:
            continue
        first, last = index == 0, index == len(wanted) - 1
        if last and actual.rstrip(_EDGE) == expected.rstrip(_EDGE):
            continue
        if first and actual.lstrip(_EDGE) == expected.lstrip(_EDGE):
            continue
        return False
    return True


def find_text(data: bytes, needle: str,
              page: Optional[int] = None) -> List[Occurrence]:
    """Locate a phrase and return where it sits, in PDF coordinates.

    Matching runs over whole words, so a phrase spanning several words is
    found as one region. Case-insensitive, because that is what someone
    typing into a search box expects.
    """
    if not needle.strip():
        return []

    wanted = needle.split()
    found: List[Occurrence] = []

    for page_text in extract(data):
        if page is not None and page_text.page != page:
            continue

        words = page_text.words
        for start in range(len(words) - len(wanted) + 1):
            window = words[start:start + len(wanted)]
            if not _window_matches(window, wanted):
                continue

            x0 = min(w.x0 for w in window)
            x1 = max(w.x1 for w in window)
            y0 = min(w.y0 for w in window)
            y1 = max(w.y1 for w in window)
            found.append(Occurrence(
                page=page_text.page, text=" ".join(w.text for w in window),
                x=x0, y=y0, width=x1 - x0, height=y1 - y0,
            ))

    return found


def _space_equivalent(term: str) -> int:
    """How many spaces occupy about the same width as `term`.

    Measured in Helvetica, which is an approximation of whatever font the
    document uses, but a far better one than "one space per character".
    """
    try:
        space = pdfmetrics.stringWidth(" ", "Helvetica", 100.0)
        width = pdfmetrics.stringWidth(term, "Helvetica", 100.0)
        if space > 0:
            return max(len(term), round(width / space))
    except Exception:
        pass
    return len(term)


def _strip_selected(raw: bytes, term: str, wanted: Optional[set],
                    counter: Dict[str, int]) -> Tuple[bytes, bool]:
    """Blank occurrences of `term`, honouring which ones were asked for.

    `counter` carries the running match index across the whole page, because
    an occurrence is numbered within the page, not within one string operand.
    `wanted` of None means every occurrence.
    """
    try:
        text = raw.decode("latin-1")
    except Exception:
        return raw, False

    lowered, needle = text.lower(), term.lower()
    if not needle:
        return raw, False

    # Pad by width, not by character count. A space is roughly half the width
    # of a digit or letter, so blanking "4200" with four spaces shortens the
    # line and drags everything after it leftwards — straight into the
    # replacement text being drawn in that spot.
    padding = " " * _space_equivalent(term)

    changed = False
    position = lowered.find(needle)
    while position != -1:
        index = counter.get(term, 0)
        counter[term] = index + 1

        if wanted is None or index in wanted:
            text = text[:position] + padding + text[position + len(term):]
            lowered = text.lower()
            changed = True
            position = lowered.find(needle, position + len(padding))
        else:
            position = lowered.find(needle, position + len(term))

    return (text.encode("latin-1", errors="replace"), changed) if changed else (raw, False)


def _remove_from_streams(
    data: bytes, per_page: Dict[int, List[Tuple[str, Optional[set]]]],
) -> bytes:
    """Delete the selected occurrences of each term from the content stream."""
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            targets = per_page.get(index)
            if not targets:
                continue

            # One counter per page: occurrence numbering runs across the page.
            counters: Dict[str, int] = {}

            instructions = pikepdf.parse_content_stream(page)
            rebuilt = []
            changed = False

            for instruction in instructions:
                operands = list(instruction.operands)

                if str(instruction.operator) in TEXT_OPERATORS:
                    for position, operand in enumerate(operands):
                        if isinstance(operand, pikepdf.String):
                            value = bytes(operand)
                            for term, wanted in targets:
                                value, hit = _strip_selected(
                                    value, term, wanted, counters)
                                changed = changed or hit
                            if value != bytes(operand):
                                operands[position] = pikepdf.String(value)
                        elif isinstance(operand, pikepdf.Array):
                            items = list(operand)
                            item_changed = False
                            for inner, item in enumerate(items):
                                if isinstance(item, pikepdf.String):
                                    value = bytes(item)
                                    for term, wanted in targets:
                                        value, hit = _strip_selected(
                                            value, term, wanted, counters)
                                        item_changed = item_changed or hit
                                    if value != bytes(item):
                                        items[inner] = pikepdf.String(value)
                            if item_changed:
                                operands[position] = pikepdf.Array(items)
                                changed = True

                rebuilt.append(pikepdf.ContentStreamInstruction(
                    operands, instruction.operator))

            if changed:
                page.Contents = pdf.make_stream(
                    pikepdf.unparse_content_stream(rebuilt))

        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


def _draw(data: bytes, drawings: Dict[int, List[Tuple[Occurrence, str, Style]]]) -> bytes:
    """Paint replacement and new text onto the pages that need it.

    Goes through the engine's compose helper rather than merging pages
    directly. That gives every page its own content stream first, without
    which stamping one page can stamp every other page that happens to share
    a stream — a real hazard, since generators reuse streams freely.
    """
    from docintel.pdf.operations import PyPdfEngine

    engine = PyPdfEngine()

    def make_overlay(index: int, width: float, height: float):
        def draw(pdf):
            for spot, text, style in drawings[index]:
                font = style.resolved_font()
                size = style.size or _infer_size(spot, font)
                pdf.setFont(font, size)
                try:
                    pdf.setFillColor(HexColor(style.colour))
                except Exception:
                    pdf.setFillColorRGB(0, 0, 0)
                # `width == 0` marks text added at an explicit point, where the
                # caller supplied a baseline already.
                y = spot.y if spot.width == 0 else _baseline(spot, font, size)
                pdf.drawString(spot.x, y, text)

        return engine._overlay(width, height, draw)

    return engine._compose(
        data,
        should_stamp=lambda index: index in drawings,
        make_overlay=make_overlay,
        over=True,
    )


def _infer_size(spot: Occurrence, font: str = "Helvetica") -> float:
    """Work out what point size the original text was set at.

    Derived from its measured width rather than its height. Height is
    unreliable: the reported glyph box depends on whether the run happens to
    contain a descender, so "Amount" and "Amount pd" at the same size report
    different heights. Width scales cleanly with point size, and the original
    string is known, so one division gives the answer.
    """
    if spot.text and spot.width > 0:
        try:
            unit = pdfmetrics.stringWidth(spot.text, font, 1.0)
            if unit > 0:
                return round(spot.width / unit, 1)
        except Exception:
            pass

    # Falling back to height: the ascender-to-descender span of the standard
    # fonts is about 0.93 of the point size.
    return round(spot.height / 0.93, 1) if spot.height >= 4 else DEFAULT_SIZE


def _baseline(spot: Occurrence, font: str, size: float) -> float:
    """Convert the bottom of the glyph box to the text baseline.

    Drawing at the box bottom puts the replacement a descender's depth too
    low, which is small but immediately visible next to the untouched text on
    the same line.
    """
    try:
        _, descent = pdfmetrics.getAscentDescent(font, size)
        return spot.y - descent          # descent is negative
    except Exception:
        return spot.y + size * 0.21


def replace_size(style: Style, size: float) -> Style:
    """A copy of `style` at a different size, leaving the caller's untouched."""
    return Style(font=style.font, size=size, colour=style.colour,
                 bold=style.bold, italic=style.italic)


def estimate_width(text: str, style: Style, size: float) -> float:
    try:
        return pdfmetrics.stringWidth(text, style.resolved_font(), size)
    except Exception:
        return len(text) * size * 0.5


def apply_edits(data: bytes, edits: Sequence[Edit],
                *, verify: bool = True) -> Tuple[bytes, List[dict]]:
    """Apply text edits and return (pdf, report).

    The report names, per edit, what was found, what font was used and whether
    the replacement is wider than the text it replaced — the caller needs all
    three to tell the user what actually happened.
    """
    if not edits:
        raise PDFEngineError("No edits were supplied.")

    removals: Dict[int, List[Tuple[str, Optional[set]]]] = {}
    drawings: Dict[int, List[Tuple[Occurrence, str, Style]]] = {}
    report: List[dict] = []

    for edit in edits:
        if not edit.find.strip():
            raise PDFEngineError("The text to find cannot be empty.")

        spots = find_text(data, edit.find, page=edit.page)
        if not spots:
            raise PDFEngineError(
                f"'{edit.find}' was not found on page {edit.page}. "
                "Text must match exactly as it appears in the document."
            )

        if edit.occurrence is not None:
            if edit.occurrence >= len(spots):
                raise PDFEngineError(
                    f"Occurrence {edit.occurrence + 1} of '{edit.find}' does not "
                    f"exist; there are {len(spots)}."
                )
            wanted = {edit.occurrence}
            spots = [spots[edit.occurrence]]
        else:
            wanted = None

        removals.setdefault(edit.page, []).append((edit.find, wanted))

        for spot in spots:
            size = edit.style.size or _infer_size(spot, edit.style.resolved_font())
            entry = {
                "page": edit.page,
                "found": spot.text,
                "replaced_with": edit.replace,
                "occurrences": len(spots),
                "font": edit.style.resolved_font() if edit.replace else None,
                "size": size if edit.replace else None,
                "overflows": False,
            }

            if edit.replace:
                style = edit.style
                new_width = estimate_width(edit.replace, style, size)

                # An explicitly chosen size is an instruction, not a
                # suggestion: shrinking it silently would override the user.
                # Fitting applies only where the size was inferred.
                may_fit = edit.fit_to_width and edit.style.size is None

                if new_width > spot.width * 1.02 and may_fit:
                    # Scale down to the original width rather than overrunning
                    # the text that follows. Aim slightly inside it: the width
                    # is measured with our font against a span measured from
                    # the document's font, so the estimate carries a little
                    # error, and landing a hair short is invisible while
                    # landing a hair long touches the next word.
                    fitted = size * (spot.width * 0.97 / new_width)
                    floor = size * MIN_FIT_RATIO
                    if fitted >= floor:
                        style = replace_size(style, round(fitted, 1))
                        size = style.size or size
                        new_width = estimate_width(edit.replace, style, size)
                        entry["size"] = size
                        entry["shrunk"] = True
                        entry["note"] = (
                            f"The replacement was set at {size}pt to fit the space "
                            "the original text occupied."
                        )

                drawings.setdefault(edit.page, []).append((spot, edit.replace, style))
                entry["font"] = style.resolved_font()

                if new_width > spot.width * 1.02:
                    entry["overflows"] = True
                    entry["note"] = (
                        "The replacement is wider than the text it replaces and "
                        "could not be shrunk enough to fit without becoming "
                        "unreadable. PDF text does not reflow, so it may overlap "
                        "what follows."
                    )
            report.append(entry)

    output = _remove_from_streams(data, removals)
    if drawings:
        output = _draw(output, drawings)

    if verify:
        _verify(output, edits)

    return output, report


def add_text(data: bytes, page: int, x: float, y: float, text: str,
             style: Optional[Style] = None, *, verify: bool = True) -> bytes:
    """Draw new text at a point on a page, in PDF coordinates."""
    if not text.strip():
        raise PDFEngineError("The text to add cannot be empty.")

    style = style or Style()
    reader = PdfReader(io.BytesIO(data))
    if page < 1 or page > len(reader.pages):
        raise PDFEngineError(
            f"Page {page} does not exist; the document has {len(reader.pages)}."
        )

    spot = Occurrence(page=page, text=text, x=x, y=y,
                      width=0, height=style.size or DEFAULT_SIZE)
    output = _draw(data, {page: [(spot, text, style)]})

    if verify:
        pages = {p.page: p.text for p in extract(output)}
        if _normalise(text) not in _normalise(pages.get(page, "")):
            raise PDFEngineError(
                "The text was drawn but could not be read back, so it was "
                "discarded rather than saved as an edit that did not take."
            )
    return output


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


def _verify(output: bytes, edits: Sequence[Edit]) -> None:
    """Re-parse the result and confirm each edit actually took effect."""
    pages = {p.page: p.text for p in extract(output)}

    for edit in edits:
        page_text = _normalise(pages.get(edit.page, ""))

        # Deleting every occurrence must leave none behind. Two cases are
        # exempt: a named occurrence legitimately leaves the others, and a
        # replacement that contains the original ("4200" -> "42000") makes the
        # substring check meaningless rather than failing.
        contains_original = (
            edit.replace and _normalise(edit.find) in _normalise(edit.replace)
        )
        if (edit.occurrence is None and not contains_original
                and _normalise(edit.find) in page_text):
            raise PDFEngineError(
                f"'{edit.find}' is still present on page {edit.page} after the "
                "edit, so the result was discarded rather than returned as a "
                "document that was not actually changed."
            )

        if edit.replace and _normalise(edit.replace) not in page_text:
            raise PDFEngineError(
                f"The replacement text could not be read back on page "
                f"{edit.page}, so the result was discarded."
            )
