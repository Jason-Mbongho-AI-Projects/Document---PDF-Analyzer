"""
Document properties: metadata, bookmarks, and removing hidden data.

Three things Acrobat exposes that a viewer alone does not, grouped because
they all concern the document's structure rather than its visible pages.

Sanitising deserves a note. A PDF carries more than it shows: author names,
the software that produced it, editing history in XMP, embedded files,
JavaScript, and form values left in fields. Sending a document without
clearing those has leaked more confidential information than most redaction
failures. What this removes is listed explicitly, and what it cannot promise
is stated in the same breath.
"""
from __future__ import annotations

import io
from typing import Dict, List, Optional

import pikepdf
from pypdf import PdfReader, PdfWriter

from docintel.pdf.engine import PDFEngineError

# The document-information fields worth surfacing and editing.
FIELDS = ("title", "author", "subject", "keywords", "creator", "producer")


def read_properties(data: bytes) -> dict:
    """Metadata, page geometry and what hidden data the file carries."""
    try:
        reader = PdfReader(io.BytesIO(data))
        info = reader.metadata or {}

        pages = []
        for number, page in enumerate(reader.pages, start=1):
            box = page.mediabox
            pages.append({
                "page": number,
                "width": round(float(box.width), 1),
                "height": round(float(box.height), 1),
                "rotation": int(page.get("/Rotate", 0) or 0),
            })

        return {
            "metadata": {
                field: str(getattr(info, field, None) or "")
                for field in FIELDS
            },
            "page_count": len(reader.pages),
            "encrypted": reader.is_encrypted,
            "pages": pages,
            "hidden_data": _hidden_data(data),
        }
    except Exception as exc:
        raise PDFEngineError(f"The document properties could not be read: {exc}")


def _hidden_data(data: bytes) -> dict:
    """What the file carries beyond its visible content."""
    found = {
        "xmp_metadata": False,
        "embedded_files": 0,
        "javascript": False,
        "form_fields": 0,
        "outline_entries": 0,
        "annotations": 0,
    }
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            root = pdf.Root
            found["xmp_metadata"] = "/Metadata" in root

            names = root.get("/Names", {})
            if "/EmbeddedFiles" in names:
                embedded = names["/EmbeddedFiles"].get("/Names", [])
                found["embedded_files"] = len(embedded) // 2
            found["javascript"] = "/JavaScript" in names or "/OpenAction" in root

            acro = root.get("/AcroForm")
            if acro is not None and "/Fields" in acro:
                found["form_fields"] = len(acro["/Fields"])

            if "/Outlines" in root:
                found["outline_entries"] = _count_outline(root["/Outlines"])

            for page in pdf.pages:
                found["annotations"] += len(page.get("/Annots", []))
    except Exception:
        # A file too damaged to inspect is reported as carrying nothing rather
        # than failing the whole properties call.
        pass
    return found


def _count_outline(node, depth: int = 0) -> int:
    if depth > 32:            # cyclic outlines exist in the wild
        return 0
    total = 0
    try:
        child = node.get("/First")
        seen = 0
        while child is not None and seen < 5000:
            total += 1 + _count_outline(child, depth + 1)
            child = child.get("/Next")
            seen += 1
    except Exception:
        pass
    return total


def set_metadata(data: bytes, values: Dict[str, str]) -> bytes:
    """Write the document-information fields, leaving the rest untouched."""
    unknown = [k for k in values if k not in FIELDS]
    if unknown:
        raise PDFEngineError(
            f"Unknown propert{'y' if len(unknown) == 1 else 'ies'}: "
            f"{', '.join(unknown)}. Valid: {', '.join(FIELDS)}."
        )

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=io.BytesIO(data))

    existing = {}
    if reader.metadata:
        for field in FIELDS:
            current = getattr(reader.metadata, field, None)
            if current:
                existing[f"/{field.capitalize()}"] = str(current)

    for field, value in values.items():
        key = f"/{field.capitalize()}"
        if value:
            existing[key] = value
        else:
            existing.pop(key, None)

    writer.add_metadata(existing)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def sanitise(data: bytes, *, remove_metadata: bool = True,
             remove_javascript: bool = True,
             remove_embedded_files: bool = True,
             remove_outline: bool = False) -> tuple[bytes, List[str]]:
    """Strip hidden data. Returns the document and what was actually removed.

    The report lists only what was present and taken out, so a caller can tell
    the difference between "cleaned" and "there was nothing to clean" instead
    of showing a reassuring message either way.
    """
    removed: List[str] = []

    try:
        with pikepdf.open(io.BytesIO(data), allow_overwriting_input=False) as pdf:
            root = pdf.Root

            if remove_metadata:
                if "/Metadata" in root:
                    del root["/Metadata"]
                    removed.append("XMP metadata")
                # pikepdf dictionaries have no clear(); the keys go one by one.
                keys = list(pdf.docinfo.keys()) if pdf.docinfo is not None else []
                if keys:
                    for key in keys:
                        del pdf.docinfo[key]
                    removed.append("document information (author, producer…)")

            names = root.get("/Names")

            if remove_javascript:
                if "/OpenAction" in root:
                    del root["/OpenAction"]
                    removed.append("document open action")
                if names is not None and "/JavaScript" in names:
                    del names["/JavaScript"]
                    removed.append("embedded JavaScript")

            if remove_embedded_files and names is not None:
                if "/EmbeddedFiles" in names:
                    del names["/EmbeddedFiles"]
                    removed.append("embedded files")

            if remove_outline and "/Outlines" in root:
                del root["/Outlines"]
                removed.append("bookmarks")

            out = io.BytesIO()
            pdf.save(out)
            return out.getvalue(), removed
    except Exception as exc:
        raise PDFEngineError(f"The document could not be sanitised: {exc}")


# ------------------------------------------------------------- bookmarks

def read_outline(data: bytes) -> List[dict]:
    """The bookmark tree, flattened with a depth on each entry."""
    entries: List[dict] = []
    try:
        reader = PdfReader(io.BytesIO(data))

        def walk(items, depth: int):
            for item in items:
                if isinstance(item, list):
                    walk(item, depth + 1)
                    continue
                try:
                    page = reader.get_destination_page_number(item) + 1
                except Exception:
                    page = None
                entries.append({
                    "title": str(item.title), "page": page, "depth": depth,
                })

        walk(reader.outline, 0)
    except Exception:
        return []
    return entries


def set_outline(data: bytes, entries: List[dict]) -> bytes:
    """Replace the bookmarks with the given flat list.

    Each entry is {title, page, depth}. Depth nests an entry under the last
    one shallower than it, which is how a flat list from a UI maps onto the
    tree a PDF actually stores.
    """
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)

    writer = PdfWriter(clone_from=io.BytesIO(data))
    # Start from nothing: this replaces rather than appends, so re-saving does
    # not accumulate duplicate bookmarks.
    try:
        writer._root_object.pop("/Outlines", None)
    except Exception:
        pass

    parents: Dict[int, object] = {}
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        if not title:
            raise PDFEngineError("A bookmark must have a title.")

        page = int(entry.get("page") or 1)
        if page < 1 or page > total:
            raise PDFEngineError(
                f"Bookmark '{title}' points at page {page}; the document has "
                f"{total}."
            )

        depth = max(0, int(entry.get("depth") or 0))
        parent = parents.get(depth - 1) if depth else None
        created = writer.add_outline_item(title, page - 1, parent=parent)
        parents[depth] = created
        # Anything deeper than this entry is no longer a valid parent.
        for deeper in [d for d in parents if d > depth]:
            parents.pop(deeper, None)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
