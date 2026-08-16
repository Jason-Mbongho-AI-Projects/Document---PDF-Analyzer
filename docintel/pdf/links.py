"""
Hyperlinks: listing, adding and removing them.

A link in a PDF is an annotation with a rectangle and an action. Two kinds
matter here: a URI action pointing somewhere on the web, and a GoTo action
pointing at another page of the same document.

Links are worth surfacing for a reason beyond convenience. The security
scanner already reports the URLs a document contains, but until now there was
no way to see where they sit or take a bad one out. Removing a link is the
counterpart to detecting it.
"""
from __future__ import annotations

import io
from typing import List, Optional
from urllib.parse import urlparse

import pikepdf

from docintel.pdf.engine import PDFEngineError

# Schemes allowed when adding a link. Anything else — javascript:, file:,
# data: — is a way to smuggle behaviour into a document that looks inert.
SAFE_SCHEMES = {"http", "https", "mailto"}


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise PDFEngineError("A link needs a destination.")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in SAFE_SCHEMES:
        raise PDFEngineError(
            f"'{parsed.scheme or url[:20]}' links are not allowed. Use "
            f"{', '.join(sorted(SAFE_SCHEMES))}."
        )
    return url


def list_links(data: bytes) -> List[dict]:
    """Every link in the document, with where it sits and where it goes."""
    found: List[dict] = []
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                height = float(page.MediaBox[3]) - float(page.MediaBox[1])
                for index, annot in enumerate(page.get("/Annots", []) or []):
                    try:
                        if annot.get("/Subtype") != "/Link":
                            continue
                        rect = [float(v) for v in annot.get("/Rect", [0, 0, 0, 0])]
                        action = annot.get("/A", {})
                        target, kind = None, "unknown"

                        if action.get("/S") == "/URI":
                            target = str(action.get("/URI", ""))
                            kind = "uri"
                        elif "/Dest" in annot or action.get("/S") == "/GoTo":
                            kind = "page"
                            target = "elsewhere in this document"

                        x0, y0, x1, y1 = rect
                        found.append({
                            "page": number,
                            "index": index,
                            "kind": kind,
                            "target": target,
                            # View coordinates, matching how annotations are
                            # reported everywhere else in the API.
                            "rect": {
                                "x": round(min(x0, x1), 2),
                                "y": round(height - max(y0, y1), 2),
                                "width": round(abs(x1 - x0), 2),
                                "height": round(abs(y1 - y0), 2),
                            },
                        })
                    except Exception:
                        # A malformed annotation should not hide the rest.
                        continue
    except Exception as exc:
        raise PDFEngineError(f"The links could not be read: {exc}")
    return found


def add_link(data: bytes, *, page: int, rect: dict, url: str) -> bytes:
    """Add a clickable area on `page` pointing at `url`.

    `rect` is in view coordinates — x and y measured from the top-left — as
    everything else in this API reports them.
    """
    url = _check_url(url)

    for key in ("x", "y", "width", "height"):
        if key not in rect:
            raise PDFEngineError(f"The link area is missing '{key}'.")
    if rect["width"] <= 0 or rect["height"] <= 0:
        raise PDFEngineError("A link area needs a width and a height.")

    with pikepdf.open(io.BytesIO(data)) as pdf:
        if page < 1 or page > len(pdf.pages):
            raise PDFEngineError(
                f"Page {page} does not exist; the document has {len(pdf.pages)}."
            )

        target = pdf.pages[page - 1]
        height = float(target.MediaBox[3]) - float(target.MediaBox[1])
        y_top = height - rect["y"]
        y_bottom = y_top - rect["height"]

        annotation = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            Rect=[rect["x"], y_bottom, rect["x"] + rect["width"], y_top],
            # No visible frame: a border box around every link is rarely what
            # anyone wants, and the area is still clickable without one.
            Border=[0, 0, 0],
            A=pikepdf.Dictionary(
                S=pikepdf.Name("/URI"),
                URI=pikepdf.String(url),
            ),
        ))

        if "/Annots" in target:
            target.Annots.append(annotation)
        else:
            target.Annots = pdf.make_indirect([annotation])

        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


def remove_links(data: bytes, *, page: Optional[int] = None,
                 index: Optional[int] = None) -> tuple[bytes, int]:
    """Remove links. Returns the document and how many went.

    With no arguments every link in the document is removed, which is the
    usual reason for calling this: stripping the links out of something before
    passing it on.
    """
    removed = 0
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for number, target in enumerate(pdf.pages, start=1):
            if page is not None and number != page:
                continue
            annots = target.get("/Annots")
            if not annots:
                continue

            keep = []
            for position, annot in enumerate(annots):
                is_link = False
                try:
                    is_link = annot.get("/Subtype") == "/Link"
                except Exception:
                    is_link = False

                if is_link and (index is None or position == index):
                    removed += 1
                    continue
                keep.append(annot)

            if keep:
                target.Annots = pdf.make_indirect(keep)
            elif "/Annots" in target:
                del target["/Annots"]

        if removed == 0:
            raise PDFEngineError("There were no links to remove.")

        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue(), removed
