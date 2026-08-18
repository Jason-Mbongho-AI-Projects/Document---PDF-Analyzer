"""
Working out what an uploaded file actually is.

The platform works on PDFs, but people arrive with Word files, spreadsheets,
scans and screenshots, so anything convertible is accepted and turned into a
PDF at the door.

Widening what is accepted must not become "trust the extension". A filename
and a declared content type are both chosen by whoever is uploading; the bytes
are not. Every format here is recognised by its own signature, and the
extension only breaks ties between formats that share one — the OOXML family
are all ZIP archives, so the archive is opened and its parts inspected rather
than believed.

Plain text has no signature, which is precisely why it is the last resort: a
file is only treated as text once every binary signature has failed and the
bytes decode cleanly with no NUL in them.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Optional

# Signature → format id. Checked in order; the first match wins.
SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"{\rtf", "rtf"),
    # Legacy Office (.doc/.xls/.ppt) is an OLE compound file. It is recognised
    # so the error can name the format, not so it can be trusted apart.
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
]

# A PDF header may sit behind a little junk; the spec tolerates an offset.
PDF_HEADER_WINDOW = 1024

# Formats that are archives underneath, told apart by what they contain.
OOXML_PARTS = {
    "word/": "docx",
    "xl/": "xlsx",
    "ppt/": "pptx",
}

# Executables and archives that are never a document, listed so the refusal
# can say what the file actually is instead of "not a PDF".
HOSTILE = [
    (b"MZ", "a Windows executable"),
    (b"\x7fELF", "a Linux executable"),
    (b"\xca\xfe\xba\xbe", "a Java class file"),
    (b"#!", "a shell script"),
    (b"Rar!\x1a\x07", "a RAR archive"),
    (b"\x1f\x8b", "a gzip archive"),
    (b"7z\xbc\xaf\x27\x1c", "a 7-Zip archive"),
]


@dataclass
class Detected:
    """What the bytes are, and whether the platform can take them."""
    format: str                 # "pdf", "docx", "png", "txt", "unknown"…
    label: str                  # for a human
    convertible: bool           # can become a PDF
    reason: str = ""            # why not, when it cannot


LABELS = {
    "pdf": "PDF", "docx": "Word document", "xlsx": "Excel workbook",
    "pptx": "PowerPoint presentation", "odt": "OpenDocument text",
    "rtf": "Rich Text", "html": "HTML", "csv": "CSV", "md": "Markdown",
    "txt": "plain text", "png": "PNG image", "jpg": "JPEG image",
    "gif": "GIF image", "bmp": "BMP image", "tiff": "TIFF image",
    "webp": "WebP image",
}


def _zip_format(data: bytes) -> Optional[str]:
    """Distinguish the ZIP-based formats by looking inside the archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except Exception:
        return None

    # OpenDocument declares itself in a mimetype part.
    if "mimetype" in names:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                declared = archive.read("mimetype").decode("ascii", "ignore")
            if "opendocument.text" in declared:
                return "odt"
        except Exception:
            pass

    for prefix, fmt in OOXML_PARTS.items():
        if any(name.startswith(prefix) for name in names):
            return fmt
    return None


def _looks_textual(data: bytes) -> bool:
    """True when the bytes are plausibly text a person wrote.

    A NUL byte is the giveaway for binary content; text files do not contain
    them. Anything that fails to decode is not text either.
    """
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            sample.decode(encoding)
            return True
        except (UnicodeDecodeError, UnicodeError):
            continue
    return False


def _textual_kind(data: bytes, extension: str) -> str:
    """Which flavour of text, using the extension only to choose a renderer.

    The content is already known to be text, so the extension cannot be used
    to smuggle anything — it only decides whether to lay the file out as a
    table, as Markdown, or as plain paragraphs.
    """
    if extension in ("csv", "tsv"):
        return "csv"
    if extension in ("md", "markdown"):
        return "md"
    if extension in ("html", "htm"):
        return "html"

    head = data[:512].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return "html"
    return "txt"


def detect(data: bytes, filename: str = "") -> Detected:
    """Identify an upload from its content."""
    if not data:
        return Detected("empty", "an empty file", False,
                        "The uploaded file is empty.")

    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # A PDF is allowed a short run-up before its header.
    if b"%PDF-" in data[:PDF_HEADER_WINDOW]:
        return Detected("pdf", "PDF", True)

    for signature, description in HOSTILE:
        if data.startswith(signature):
            return Detected("blocked", description, False,
                            f"This is {description}, not a document.")

    if data.startswith(b"PK\x03\x04"):
        fmt = _zip_format(data)
        if fmt:
            return Detected(fmt, LABELS[fmt], True)
        return Detected("zip", "a ZIP archive", False,
                        "ZIP archives are not documents. Upload the file "
                        "inside it instead.")

    for signature, fmt in SIGNATURES:
        if data.startswith(signature):
            if fmt == "ole":
                return Detected(
                    "ole", "a legacy Office document", False,
                    "Word 97-2003, Excel 97-2003 and PowerPoint 97-2003 files "
                    "are not supported. Save it as .docx, .xlsx or .pptx and "
                    "upload that.")
            return Detected(fmt, LABELS.get(fmt, fmt), True)

    # WebP is RIFF-framed, so its marker sits past the start.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return Detected("webp", LABELS["webp"], True)

    if _looks_textual(data):
        kind = _textual_kind(data, extension)
        return Detected(kind, LABELS[kind], True)

    return Detected("unknown", "an unrecognised format", False,
                    "This file is not a document format the platform can "
                    "read. Convert it to PDF and upload that.")
