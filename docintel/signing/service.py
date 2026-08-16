"""
Signing: personal Fill & Sign, and the multi-party signature request workflow.

What this is and is not, stated plainly because it matters legally:

This produces a *visible* signature — an image or rendered name stamped onto
the page — together with a tamper-evident audit trail. It does NOT apply a
cryptographic digital signature (PAdES/CAdES). An image of a signature proves
nothing on its own; what gives it weight is the surrounding evidence, so the
evidence is what this module is careful about:

  * the document's hash is captured when the request is sent, so any later
    change to the source is detectable;
  * every event (sent, viewed, signed, declined) is appended to an immutable
    trail with actor, timestamp and IP;
  * each recipient gets an unguessable token rather than a shared link;
  * the signed output is a new version — the original is never overwritten.

Whether that meets a given jurisdiction's requirements is a legal question,
not a technical one, and nothing here claims it does.
"""
import io
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from docintel.db.models import (
    Document, RecipientState, SignatureEvent, SignatureFieldPlacement,
    SignatureFieldType, SignatureRecipient, SignatureRequest,
    SignatureRequestState, User, utcnow,
)
from docintel.pdf.engine import PDFEngineError
from docintel.services import documents as docsvc
from docintel.storage import content_hash

LEGAL_NOTICE = (
    "This applies a visible signature and records an audit trail. It is not a "
    "cryptographic digital signature (PAdES). Whether it satisfies a given "
    "jurisdiction's requirements for electronic signatures is a legal question "
    "and is not asserted here."
)

# Which transitions are permitted. Anything else is rejected rather than
# silently applied, so a request cannot be resurrected after completion.
ALLOWED: Dict[SignatureRequestState, set] = {
    SignatureRequestState.DRAFT: {
        SignatureRequestState.SENT, SignatureRequestState.CANCELLED,
    },
    SignatureRequestState.SENT: {
        SignatureRequestState.DELIVERED, SignatureRequestState.VIEWED,
        SignatureRequestState.PARTIALLY_SIGNED, SignatureRequestState.COMPLETED,
        SignatureRequestState.DECLINED, SignatureRequestState.CANCELLED,
        SignatureRequestState.EXPIRED,
    },
    SignatureRequestState.DELIVERED: {
        SignatureRequestState.VIEWED, SignatureRequestState.PARTIALLY_SIGNED,
        SignatureRequestState.COMPLETED, SignatureRequestState.DECLINED,
        SignatureRequestState.CANCELLED, SignatureRequestState.EXPIRED,
    },
    SignatureRequestState.VIEWED: {
        SignatureRequestState.PARTIALLY_SIGNED, SignatureRequestState.COMPLETED,
        SignatureRequestState.DECLINED, SignatureRequestState.CANCELLED,
        SignatureRequestState.EXPIRED,
    },
    SignatureRequestState.PARTIALLY_SIGNED: {
        SignatureRequestState.COMPLETED, SignatureRequestState.DECLINED,
        SignatureRequestState.CANCELLED, SignatureRequestState.EXPIRED,
    },
    SignatureRequestState.COMPLETED: set(),
    SignatureRequestState.DECLINED: set(),
    SignatureRequestState.EXPIRED: set(),
    SignatureRequestState.CANCELLED: set(),
}


class SigningError(RuntimeError):
    """User-safe signing failure."""


def new_token() -> str:
    return secrets.token_urlsafe(36)


def record_event(session: Session, request: SignatureRequest, event: str, *,
                 recipient: Optional[SignatureRecipient] = None,
                 actor: Optional[str] = None, detail: Optional[str] = None,
                 ip_address: Optional[str] = None) -> SignatureEvent:
    entry = SignatureEvent(
        request_id=request.id,
        recipient_id=recipient.id if recipient else None,
        actor=actor or (recipient.email if recipient else None),
        event=event,
        detail=(detail or "")[:500] or None,
        ip_address=ip_address,
        document_hash=request.document_hash,
    )
    session.add(entry)
    return entry


def transition(session: Session, request: SignatureRequest,
               target: SignatureRequestState, *, detail: str = "") -> None:
    if target == request.state:
        return
    if target not in ALLOWED[request.state]:
        raise SigningError(
            f"A request that is {request.state.value} cannot become "
            f"{target.value}."
        )
    previous = request.state
    request.state = target
    record_event(session, request, f"state.{target.value}",
                 detail=detail or f"from {previous.value}")


# ------------------------------------------------------------- rendering

def render_typed_signature(name: str, *, width: int = 420,
                           height: int = 120) -> bytes:
    """Render a typed name as a signature image."""
    from PIL import Image, ImageDraw, ImageFont

    if not name.strip():
        raise SigningError("A name is required.")

    image = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    font = None
    for candidate in ("georgiai.ttf", "Georgia Italic.ttf", "times.ttf", "DejaVuSerif.ttf"):
        try:
            font = ImageFont.truetype(candidate, 56)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    box = draw.textbbox((0, 0), name, font=font)
    draw.text(
        ((width - (box[2] - box[0])) / 2, (height - (box[3] - box[1])) / 2 - box[1]),
        name, font=font, fill=(15, 23, 42, 255),
    )

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def normalise_signature_image(raw: bytes, *, max_edge: int = 800) -> tuple:
    """Validate and normalise an uploaded or drawn signature image."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise SigningError(f"That file is not a readable image: {exc}") from exc

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    if max(image.size) > max_edge:
        ratio = max_edge / max(image.size)
        image = image.resize(
            (int(image.width * ratio), int(image.height * ratio)),
            Image.LANCZOS,
        )

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue(), image.width, image.height


@dataclass
class Placement:
    page: int
    x: float
    y: float           # view coordinates: origin top-left
    width: float
    height: float
    kind: str = "signature"
    text: str = ""
    image: Optional[bytes] = None


def stamp(data: bytes, placements: Sequence[Placement]) -> bytes:
    """Draw signatures, text and marks onto the PDF."""
    import pypdf
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    if not placements:
        raise SigningError("Nothing to place.")

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    for placement in placements:
        if placement.page < 1 or placement.page > total:
            raise SigningError(
                f"Page {placement.page} is out of range (1..{total})."
            )

    writer = PdfWriter(clone_from=io.BytesIO(data))

    # Same shared-content-stream hazard as watermarking: give each page its own
    # /Contents before merging, or one page's stamp lands on all of them.
    add = getattr(writer, "add_object", None) or writer._add_object
    for page in writer.pages:
        contents = page.get_contents()
        if contents is None:
            continue
        stream = DecodedStreamObject()
        stream.set_data(contents.get_data())
        page[NameObject("/Contents")] = add(stream)

    by_page: Dict[int, List[Placement]] = {}
    for placement in placements:
        by_page.setdefault(placement.page, []).append(placement)

    for number, items in by_page.items():
        page = writer.pages[number - 1]
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        buffer = io.BytesIO()
        overlay = canvas.Canvas(buffer, pagesize=(page_width, page_height))

        for item in items:
            # Convert view coordinates (top-left origin) to PDF space.
            y = page_height - item.y - item.height

            if item.image:
                from PIL import Image
                picture = Image.open(io.BytesIO(item.image))
                overlay.drawImage(
                    ImageReader(picture), item.x, y, item.width, item.height,
                    mask="auto", preserveAspectRatio=True, anchor="sw",
                )
            elif item.text:
                size = max(min(item.height * 0.62, 28), 6)
                overlay.setFont("Helvetica", size)
                overlay.setFillColorRGB(0.06, 0.09, 0.16)
                overlay.drawString(item.x + 2, y + (item.height - size) / 2 + 2,
                                   item.text[:200])

        overlay.save()
        buffer.seek(0)
        page.merge_page(PdfReader(buffer).pages[0], over=True)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


# --------------------------------------------------------- request flow

def create_request(session: Session, document: Document, user: User, *,
                   title: str, message: str = "", sequential: bool = False,
                   recipients: Sequence[dict], fields: Sequence[dict],
                   source_version: Optional[int] = None) -> SignatureRequest:
    if not recipients:
        raise SigningError("A signature request needs at least one recipient.")

    seen = set()
    for recipient in recipients:
        email = (recipient.get("email") or "").strip().lower()
        if not email:
            raise SigningError("Every recipient needs an email address.")
        if email in seen:
            raise SigningError(f"{email} is listed more than once.")
        seen.add(email)

    request = SignatureRequest(
        workspace_id=document.workspace_id,
        document_id=document.id,
        created_by=user.id,
        title=title.strip() or document.filename,
        message=message.strip() or None,
        sequential=sequential,
        source_version=source_version,
    )
    session.add(request)
    session.flush()

    created: List[SignatureRecipient] = []
    for order, recipient in enumerate(recipients, start=1):
        row = SignatureRecipient(
            request_id=request.id,
            email=(recipient["email"]).strip().lower(),
            name=(recipient.get("name") or "").strip() or None,
            order=int(recipient.get("order", order)),
            access_token=new_token(),
        )
        session.add(row)
        created.append(row)
    session.flush()

    by_email = {r.email: r for r in created}
    for field in fields:
        email = (field.get("recipient_email") or "").strip().lower()
        recipient = by_email.get(email)
        if email and recipient is None:
            raise SigningError(f"Field assigned to unknown recipient '{email}'.")

        try:
            field_type = SignatureFieldType(field["type"])
        except (KeyError, ValueError):
            raise SigningError(f"Unknown field type '{field.get('type')}'.")

        session.add(SignatureFieldPlacement(
            request_id=request.id,
            recipient_id=recipient.id if recipient else None,
            type=field_type,
            page=int(field["page"]),
            x=float(field["x"]), y=float(field["y"]),
            width=float(field["width"]), height=float(field["height"]),
            required=bool(field.get("required", True)),
            label=(field.get("label") or None),
        ))

    record_event(session, request, "request.created", actor=user.email,
                 detail=f"{len(created)} recipient(s)")
    session.flush()
    return request


def send(session: Session, request: SignatureRequest, user: User, *,
         ip_address: Optional[str] = None) -> SignatureRequest:
    if request.state != SignatureRequestState.DRAFT:
        raise SigningError("Only a draft request can be sent.")
    if not request.recipients:
        raise SigningError("Add at least one recipient before sending.")
    if not request.fields:
        raise SigningError("Place at least one field before sending.")

    document = session.get(Document, request.document_id)
    data = docsvc.read_version(session, document, request.source_version)

    # Freeze what is being signed.
    request.document_hash = content_hash(data)
    if request.source_version is None:
        request.source_version = docsvc.latest_version(session, document).version

    transition(session, request, SignatureRequestState.SENT)
    record_event(session, request, "request.sent", actor=user.email,
                 detail=f"hash {request.document_hash[:16]}…", ip_address=ip_address)

    for recipient in request.recipients:
        recipient.state = RecipientState.PENDING
    session.flush()
    return request


def recipient_by_token(session: Session, token: str) -> SignatureRecipient:
    recipient = session.scalar(
        select(SignatureRecipient).where(SignatureRecipient.access_token == token)
    )
    if recipient is None:
        raise SigningError("This signing link is not valid.")
    return recipient


def _turn_is_theirs(request: SignatureRequest, recipient: SignatureRecipient) -> bool:
    if not request.sequential:
        return True
    earlier = [
        r for r in request.recipients
        if r.order < recipient.order and r.state != RecipientState.SIGNED
    ]
    return not earlier


def open_for_signing(session: Session, token: str, *,
                     ip_address: Optional[str] = None) -> dict:
    recipient = recipient_by_token(session, token)
    request = recipient.request

    if request.state in (SignatureRequestState.CANCELLED,
                         SignatureRequestState.EXPIRED,
                         SignatureRequestState.DECLINED):
        raise SigningError(f"This request is {request.state.value}.")

    if request.expires_at and request.expires_at < datetime.now(timezone.utc):
        transition(session, request, SignatureRequestState.EXPIRED)
        session.flush()
        raise SigningError("This signing link has expired.")

    if recipient.state == RecipientState.PENDING:
        recipient.state = RecipientState.VIEWED
        recipient.viewed_at = utcnow()
        record_event(session, request, "recipient.viewed", recipient=recipient,
                     ip_address=ip_address)
        if request.state in (SignatureRequestState.SENT,
                             SignatureRequestState.DELIVERED):
            transition(session, request, SignatureRequestState.VIEWED)

    session.flush()

    mine = [f for f in request.fields if f.recipient_id == recipient.id]
    return {
        "request_id": request.id,
        "title": request.title,
        "message": request.message,
        "state": request.state.value,
        "your_turn": _turn_is_theirs(request, recipient),
        "recipient": {
            "email": recipient.email, "name": recipient.name,
            "state": recipient.state.value, "order": recipient.order,
        },
        "fields": [
            {
                "id": f.id, "type": f.type.value, "page": f.page,
                "x": f.x, "y": f.y, "width": f.width, "height": f.height,
                "required": f.required, "label": f.label, "value": f.value,
            }
            for f in mine
        ],
        "legal_notice": LEGAL_NOTICE,
    }


def submit_signature(session: Session, token: str, values: Dict[str, str], *,
                     signature_images: Optional[Dict[str, bytes]] = None,
                     ip_address: Optional[str] = None) -> dict:
    """Record one recipient's completed fields."""
    recipient = recipient_by_token(session, token)
    request = recipient.request

    if recipient.state == RecipientState.SIGNED:
        raise SigningError("You have already signed this document.")
    if recipient.state == RecipientState.DECLINED:
        raise SigningError("You declined this request.")
    if request.state in (SignatureRequestState.CANCELLED,
                         SignatureRequestState.EXPIRED,
                         SignatureRequestState.COMPLETED,
                         SignatureRequestState.DECLINED):
        raise SigningError(f"This request is {request.state.value}.")
    if not _turn_is_theirs(request, recipient):
        raise SigningError("It is not your turn to sign yet.")

    mine = [f for f in request.fields if f.recipient_id == recipient.id]
    by_id = {f.id: f for f in mine}

    unknown = sorted(set(values) - set(by_id))
    if unknown:
        raise SigningError("A field was submitted that is not assigned to you.")

    images = signature_images or {}
    for field in mine:
        supplied = values.get(field.id)
        has_image = field.id in images
        if field.required and not (supplied or has_image):
            raise SigningError(f"'{field.label or field.type.value}' is required.")
        if supplied is not None:
            field.value = str(supplied)[:5000]
            field.filled_at = utcnow()
        if has_image:
            field.filled_at = utcnow()

    recipient.state = RecipientState.SIGNED
    recipient.signed_at = utcnow()
    record_event(session, request, "recipient.signed", recipient=recipient,
                 detail=f"{len(mine)} field(s)", ip_address=ip_address)

    outstanding = [r for r in request.recipients if r.state != RecipientState.SIGNED]
    if outstanding:
        transition(session, request, SignatureRequestState.PARTIALLY_SIGNED,
                   detail=f"{len(outstanding)} recipient(s) remaining")
    else:
        transition(session, request, SignatureRequestState.COMPLETED)
        request.completed_at = utcnow()

    session.flush()
    return {
        "state": request.state.value,
        "remaining": len(outstanding),
        "completed": not outstanding,
    }


def decline(session: Session, token: str, reason: str = "", *,
            ip_address: Optional[str] = None) -> dict:
    recipient = recipient_by_token(session, token)
    request = recipient.request

    if recipient.state == RecipientState.SIGNED:
        raise SigningError("You have already signed this document.")

    recipient.state = RecipientState.DECLINED
    recipient.declined_reason = (reason or "")[:500] or None
    record_event(session, request, "recipient.declined", recipient=recipient,
                 detail=reason[:200] or None, ip_address=ip_address)
    transition(session, request, SignatureRequestState.DECLINED)
    session.flush()
    return {"state": request.state.value}


def finalise(session: Session, request: SignatureRequest, user: User) -> int:
    """Stamp all collected values onto the document as a new version."""
    if request.state != SignatureRequestState.COMPLETED:
        raise SigningError("The request is not complete yet.")
    if request.signed_version is not None:
        return request.signed_version

    document = session.get(Document, request.document_id)
    data = docsvc.read_version(session, document, request.source_version)

    current = content_hash(data)
    if request.document_hash and current != request.document_hash:
        raise SigningError(
            "The document has changed since this request was sent, so it "
            "cannot be finalised against the version the recipients saw."
        )

    placements = [
        Placement(page=f.page, x=f.x, y=f.y, width=f.width, height=f.height,
                  kind=f.type.value, text=f.value or "")
        for f in request.fields if f.value
    ]
    if not placements:
        raise SigningError("No completed fields to stamp.")

    try:
        output = stamp(data, placements)
    except PDFEngineError as exc:
        raise SigningError(str(exc)) from exc

    result = docsvc.add_version(
        session, document, output, "signed",
        actor=user, action="signature.finalised",
        detail=f"request {request.id}",
    )
    request.signed_version = result.version.version
    record_event(session, request, "document.finalised", actor=user.email,
                 detail=f"version {result.version.version}")
    session.flush()
    return result.version.version


def audit_trail(request: SignatureRequest) -> List[dict]:
    return [
        {
            "at": event.created_at.isoformat(),
            "event": event.event,
            "actor": event.actor,
            "detail": event.detail,
            "ip_address": event.ip_address,
            "document_hash": event.document_hash,
        }
        for event in request.events
    ]
