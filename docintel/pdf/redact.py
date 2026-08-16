"""
Redaction that actually removes content.

A black rectangle drawn over text is not redaction — the glyphs remain in the
content stream and can be selected, copied, or extracted with any parser. This
module removes the characters from the page's content stream, then draws the
box over the cleared area.

The removal is verified before the result is returned: the redacted document
is re-parsed and, if any redacted string is still extractable from the target
region, the operation raises instead of handing back a document that merely
looks redacted. A failure here is loud on purpose — silently returning an
unredacted file is the worst possible outcome.

Detection of what to redact (names, emails, card numbers and so on) is
separate from applying it, so a human can review before anything is destroyed.
"""
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pikepdf

from docintel.pdf.engine import PDFEngineError
from docintel.pdf.text import extract

# ------------------------------------------------------------- detection

PATTERNS: Dict[str, re.Pattern] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3,4}[\s-]?\d{3,4}(?!\d)"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "url": re.compile(r"\bhttps?://[^\s<>\"')\]]+"),
    "date_of_birth": re.compile(
        r"\b(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:19|20)\d{2}\b"
    ),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
}


def _luhn(digits: str) -> bool:
    """Card-number checksum, so ordinary long numbers are not flagged."""
    numbers = [int(d) for d in digits if d.isdigit()]
    if not 13 <= len(numbers) <= 19:
        return False
    checksum, parity = 0, len(numbers) % 2
    for index, digit in enumerate(numbers):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


@dataclass
class Candidate:
    kind: str
    text: str
    page: int
    start: int
    end: int
    rects: List[Dict[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "text": self.text, "page": self.page,
            "start": self.start, "end": self.end, "rects": self.rects,
        }


def detect(data: bytes, kinds: Optional[Sequence[str]] = None,
           custom_terms: Optional[Sequence[str]] = None,
           custom_regex: Optional[str] = None,
           password: Optional[str] = None) -> List[Candidate]:
    """Find candidates for redaction. Nothing is modified.

    Always review these before applying — redaction is irreversible in the
    version it produces.
    """
    selected = list(kinds) if kinds else list(PATTERNS)
    unknown = [k for k in selected if k not in PATTERNS]
    if unknown:
        raise PDFEngineError(f"Unknown detector(s): {', '.join(unknown)}")

    expressions = [(k, PATTERNS[k]) for k in selected]

    for term in (custom_terms or []):
        if term.strip():
            expressions.append(("custom_term",
                                re.compile(re.escape(term.strip()), re.IGNORECASE)))

    if custom_regex:
        try:
            expressions.append(("custom_regex", re.compile(custom_regex, re.IGNORECASE)))
        except re.error as exc:
            raise PDFEngineError(f"Invalid regular expression: {exc}") from exc

    found: List[Candidate] = []
    for page in extract(data, password=password):
        for kind, expression in expressions:
            for match in expression.finditer(page.text):
                value = match.group(0)
                if kind == "credit_card" and not _luhn(value):
                    continue
                if len(value.strip()) < 3:
                    continue

                covered = [w for w in page.words
                           if w.start < match.end() and w.end > match.start()]
                rects = [
                    {k: round(v, 2)
                     for k, v in w.view_rect(page.height).items()}
                    for w in covered
                ]
                found.append(Candidate(kind, value, page.page,
                                       match.start(), match.end(), rects))

    # Deduplicate overlapping hits, keeping the longest at each position.
    found.sort(key=lambda c: (c.page, c.start, -(c.end - c.start)))
    kept: List[Candidate] = []
    for candidate in found:
        if any(candidate.page == k.page and candidate.start < k.end
               and candidate.end > k.start for k in kept):
            continue
        kept.append(candidate)
    return kept


# --------------------------------------------------------------- applying

TEXT_OPERATORS = ("Tj", "TJ", "'", '"')


def _rewrite_instructions(instructions, terms: Sequence[str]):
    """Rebuild a content stream with the target text blanked out.

    The instruction list must be rebuilt rather than mutated: pikepdf returns
    a fresh operand list on every attribute access, so assigning into
    `instruction.operands` is silently discarded.
    """
    rebuilt = []
    changed_any = False

    for instruction in instructions:
        operands = list(instruction.operands)

        if str(instruction.operator) in TEXT_OPERATORS:
            for position, operand in enumerate(operands):
                if isinstance(operand, pikepdf.String):
                    cleaned, changed = _strip_terms(bytes(operand), terms)
                    if changed:
                        operands[position] = pikepdf.String(cleaned)
                        changed_any = True

                elif isinstance(operand, pikepdf.Array):
                    # TJ takes [ (str) offset (str) offset ... ]
                    items = list(operand)
                    item_changed = False
                    for inner, item in enumerate(items):
                        if isinstance(item, pikepdf.String):
                            cleaned, changed = _strip_terms(bytes(item), terms)
                            if changed:
                                items[inner] = pikepdf.String(cleaned)
                                item_changed = True
                    if item_changed:
                        operands[position] = pikepdf.Array(items)
                        changed_any = True

        rebuilt.append((operands, instruction.operator))

    return rebuilt, changed_any


def _strip_terms(raw: bytes, terms: Sequence[str]) -> Tuple[bytes, bool]:
    """Remove any occurrence of the given terms from a string operand.

    Characters are replaced with spaces rather than deleted, so the surrounding
    text keeps roughly its original spacing instead of reflowing.
    """
    try:
        text = raw.decode("latin-1")
    except Exception:
        return raw, False

    changed = False
    for term in terms:
        if not term:
            continue
        lowered, needle = text.lower(), term.lower()
        position = lowered.find(needle)
        while position != -1:
            text = text[:position] + (" " * len(term)) + text[position + len(term):]
            changed = True
            lowered = text.lower()
            position = lowered.find(needle, position + len(term))

    return (text.encode("latin-1", errors="replace"), changed) if changed else (raw, False)


def apply(data: bytes, targets: Sequence[Candidate],
          *, draw_boxes: bool = True, verify: bool = True) -> bytes:
    """Remove the target text from the content stream and cover the area.

    Raises if verification shows any target text still extractable.
    """
    if not targets:
        raise PDFEngineError("No redaction targets were supplied.")

    by_page: Dict[int, List[Candidate]] = {}
    for candidate in targets:
        by_page.setdefault(candidate.page, []).append(candidate)

    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError as exc:
        raise PDFEngineError("This PDF is password protected.") from exc
    except Exception as exc:
        raise PDFEngineError(f"The document could not be opened: {exc}") from exc

    try:
        total = len(pdf.pages)
        for page_number, candidates in by_page.items():
            if page_number < 1 or page_number > total:
                raise PDFEngineError(f"Page {page_number} is out of range (1..{total}).")

            page = pdf.pages[page_number - 1]
            terms = [c.text for c in candidates if c.text.strip()]

            # 1. Remove the characters from the content stream.
            instructions = pikepdf.parse_content_stream(page)
            rebuilt, modified = _rewrite_instructions(instructions, terms)

            if modified:
                page.Contents = pdf.make_stream(
                    pikepdf.unparse_content_stream(rebuilt)
                )

            # 2. Cover the cleared area.
            if draw_boxes:
                _draw_boxes(pdf, page, candidates)

        buffer = io.BytesIO()
        pdf.save(buffer)
        output = buffer.getvalue()
    finally:
        pdf.close()

    if verify:
        _verify(output, targets)

    return output


def _draw_boxes(pdf, page, candidates: Sequence[Candidate]) -> None:
    """Append black rectangles over the redacted regions."""
    height = float(page.MediaBox[3]) - float(page.MediaBox[1])

    commands = ["q", "0 0 0 rg"]
    for candidate in candidates:
        for rect in candidate.rects:
            # Stored in view coordinates; convert back for the PDF.
            x = rect["x"]
            y = height - rect["y"] - rect["height"]
            pad = 1.0
            commands.append(
                f"{x - pad:.2f} {y - pad:.2f} "
                f"{rect['width'] + pad * 2:.2f} {rect['height'] + pad * 2:.2f} re f"
            )
    commands.append("Q")

    overlay = pdf.make_stream(("\n".join(commands)).encode("latin-1"))
    page.contents_add(overlay, prepend=False)


def _verify(output: bytes, targets: Sequence[Candidate]) -> None:
    """Re-parse the result and confirm the text is genuinely gone."""
    remaining: List[str] = []

    try:
        pages = {p.page: p.text for p in extract(output)}
    except Exception as exc:
        raise PDFEngineError(
            f"Redaction could not be verified because the output failed to parse: {exc}"
        ) from exc

    for candidate in targets:
        text = pages.get(candidate.page, "")
        if candidate.text and candidate.text.lower() in text.lower():
            remaining.append(candidate.text)

    if remaining:
        unique = sorted(set(remaining))[:5]
        raise PDFEngineError(
            "Redaction failed verification: the following text is still "
            f"extractable from the output — {', '.join(unique)}. "
            "The document was NOT redacted and has been discarded."
        )
