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


def validate_upload(
    filename: str,
    declared_mime: Optional[str],
    data: bytes,
) -> ValidationResult:
    safe_name = sanitize_filename(filename)

    if not data:
        return ValidationResult(False, "The uploaded file is empty.", safe_name)

    if len(data) > settings.max_upload_bytes:
        return ValidationResult(
            False,
            f"File exceeds the {settings.max_upload_mb} MB limit.",
            safe_name,
        )

    if not safe_name.lower().endswith(".pdf"):
        return ValidationResult(False, "Only .pdf files are accepted.", safe_name)

    if declared_mime and declared_mime not in settings.allowed_mime_types:
        return ValidationResult(
            False,
            f"Declared content type '{declared_mime}' is not accepted.",
            safe_name,
        )

    # The authoritative check: the bytes themselves.
    if not looks_like_pdf(data):
        return ValidationResult(
            False,
            "File content is not a PDF, regardless of its name or content type.",
            safe_name,
        )

    return ValidationResult(True, "ok", safe_name, "application/pdf")
