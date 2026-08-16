"""
Files carried inside a PDF.

A PDF can embed arbitrary files. That is genuinely useful — a spreadsheet
attached to the report it summarises — and it is also how documents smuggle
executables past mail filters, which is why the security scanner flags them.

Both facts shape this module. Attaching is supported, listing shows what is
already there, and extraction hands the bytes back with a filename that has
been stripped of any path. Nothing here executes or opens an attachment.
"""
from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

import pikepdf

from docintel.pdf.engine import PDFEngineError

# Extensions that have no business being carried inside a document. This is a
# guard against casual mistakes, not a security boundary: the scanner reports
# whatever is actually present, whatever it is called.
RISKY = {
    ".exe", ".dll", ".scr", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js",
    ".jar", ".msi", ".apk", ".sh", ".lnk",
}


def safe_name(name: str) -> str:
    """A filename with any directory part removed.

    An attachment named ../../etc/passwd must never be written anywhere near
    that path, so the name is reduced to its last component and cleaned.
    """
    base = re.split(r"[\\/]", str(name or "").strip())[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(". ")
    return base or "attachment"


def list_attachments(data: bytes) -> List[dict]:
    """What files the document carries."""
    found: List[dict] = []
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for name, spec in (pdf.attachments or {}).items():
                try:
                    stream = spec.get_file()
                    size = len(bytes(stream.read_bytes()))
                except Exception:
                    size = 0
                clean = safe_name(name)
                found.append({
                    "name": clean,
                    "size_bytes": size,
                    "risky": any(clean.lower().endswith(e) for e in RISKY),
                })
    except Exception as exc:
        raise PDFEngineError(f"The attachments could not be read: {exc}")
    return found


def attach(data: bytes, filename: str, payload: bytes,
           description: str = "") -> bytes:
    """Embed a file. The name is cleaned before it is stored."""
    if not payload:
        raise PDFEngineError("The file to attach is empty.")

    name = safe_name(filename)
    if any(name.lower().endswith(extension) for extension in RISKY):
        raise PDFEngineError(
            f"'{name}' is a kind of file that will not be embedded. Documents "
            "carrying executables are treated as malicious by most scanners, "
            "and rightly so."
        )

    with pikepdf.open(io.BytesIO(data)) as pdf:
        if name in (pdf.attachments or {}):
            raise PDFEngineError(f"'{name}' is already attached.")

        spec = pikepdf.AttachedFileSpec(pdf, payload, description=description)
        pdf.attachments[name] = spec

        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


def extract(data: bytes, name: str) -> Tuple[bytes, str]:
    """Return an attachment's bytes and its cleaned name."""
    wanted = safe_name(name)
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for key, spec in (pdf.attachments or {}).items():
            if safe_name(key) != wanted:
                continue
            try:
                return bytes(spec.get_file().read_bytes()), wanted
            except Exception as exc:
                raise PDFEngineError(f"'{wanted}' could not be read: {exc}")
    raise PDFEngineError(f"'{wanted}' is not attached to this document.")


def remove(data: bytes, name: Optional[str] = None) -> Tuple[bytes, int]:
    """Remove one attachment, or all of them when no name is given."""
    wanted = safe_name(name) if name else None
    removed = 0

    with pikepdf.open(io.BytesIO(data)) as pdf:
        for key in list((pdf.attachments or {}).keys()):
            if wanted is None or safe_name(key) == wanted:
                del pdf.attachments[key]
                removed += 1

        if removed == 0:
            raise PDFEngineError(
                f"'{wanted}' is not attached to this document." if wanted
                else "This document has no attachments."
            )

        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue(), removed
