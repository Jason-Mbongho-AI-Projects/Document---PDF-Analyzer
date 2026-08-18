"""
Upload validation. Every uploaded file is untrusted.

Extension, declared MIME type and magic bytes are all checked, because the
first two are attacker-controlled. A file only passes if its actual content
starts with a real PDF header.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from docintel.config import settings
from docintel.core import sniff

PDF_MAGIC = b"%PDF-"
# Some generators emit junk before the header; the spec allows a small offset.
MAX_HEADER_OFFSET = 1024

DANGEROUS_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass
class ValidationResult:
    ok: bool
    message: str
    safe_filename: str = ""
    detected_type: str = ""


def sanitize_filename(name: str, fallback: str = "document.pdf") -> str:
    """Reduce a client-supplied filename to something safe to store and echo.

    Only ever used for display and download headers — it never determines a
    storage location, which is derived from IDs instead.
    """
    if not name:
        return fallback

    # Strip any directory component from every convention, not just this OS.
    name = name.replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKD", name)
    name = name.replace("\x00", "")
    name = DANGEROUS_NAME_CHARS.sub("_", name).strip(". ")

    if not name:
        return fallback

    stem, _, suffix = name.rpartition(".")
    if stem.upper() in RESERVED_WINDOWS_NAMES or name.upper() in RESERVED_WINDOWS_NAMES:
        name = f"file_{name}"

    if len(name) > 200:
        stem, _, suffix = name.rpartition(".")
        name = (stem[:190] + "." + suffix) if suffix else name[:200]

    return name


def looks_like_pdf(data: bytes) -> bool:
    return PDF_MAGIC in data[:MAX_HEADER_OFFSET]



# The extension each detected format should carry once stored.
FORMAT_EXTENSIONS = {
    "pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "pptx": "pptx",
    "odt": "odt", "rtf": "rtf", "html": "html", "csv": "csv",
    "md": "md", "txt": "txt", "png": "png", "jpg": "jpg",
    "gif": "gif", "bmp": "bmp", "tiff": "tiff", "webp": "webp",
}


def align_extension(name: str, detected: str) -> str:
    """Give a filename the extension its content actually warrants.

    Content decides what a file is, so a PDF arriving as "script.exe" is
    accepted — but it must not be stored, listed and later downloaded under a
    name that claims to be executable. The name is corrected to match the
    bytes rather than the upload being refused over it.
    """
    wanted = FORMAT_EXTENSIONS.get(detected)
    if not wanted:
        return name

    stem, dot, current = name.rpartition(".")
    if dot and current.lower() in (wanted, *(
            {"jpeg"} if wanted == "jpg" else set()),
            *({"markdown"} if wanted == "md" else set()),
            *({"htm"} if wanted == "html" else set()),
            *({"tif"} if wanted == "tiff" else set())):
        return name

    base = stem if dot else name
    return f"{base or 'document'}.{wanted}"


def validate_upload(
    filename: str,
    declared_mime: Optional[str],
    data: bytes,
) -> ValidationResult:
    """Decide whether an upload can be accepted, and as what.

    Anything the platform can turn into a PDF is accepted; everything else is
    refused by name rather than with a generic rejection. The decision is made
    from the bytes — the filename and the declared content type are both
    supplied by the uploader, so neither can be the thing that lets a file in.
    """
    safe_name = sanitize_filename(filename)

    if not data:
        return ValidationResult(False, "The uploaded file is empty.", safe_name)

    if len(data) > settings.max_upload_bytes:
        return ValidationResult(
            False,
            f"File exceeds the {settings.max_upload_mb} MB limit.",
            safe_name,
        )

    found = sniff.detect(data, safe_name)
    if not found.convertible:
        return ValidationResult(False, found.reason, safe_name, found.format)

    # Correct the name to match the content before anything stores it.
    return ValidationResult(
        True, "ok", align_extension(safe_name, found.format), found.format)
