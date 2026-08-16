"""
Signing endpoints.

Two audiences, two authorization models:

  * /documents/{id}/sign*  — the sender, authenticated normally.
  * /sign/{token}          — the recipient, who has no account. Their bearer
                             credential is the unguessable per-recipient token
                             in the URL, which grants access to exactly one
                             request and nothing else in the workspace.
"""
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_document
from docintel.db.models import (
    Document, SignatureAsset, SignatureRequest, SignatureRequestState,
)
from docintel.services import documents as docsvc
from docintel.signing import service as signing
from docintel.storage import build_key, storage

router = APIRouter(tags=["signing"])


# ------------------------------------------------------------- schemas

class RecipientIn(BaseModel):
    email: EmailStr
    name: Optional[str] = Field(default=None, max_length=200)
    order: int = Field(default=1, ge=1, le=50)


class FieldIn(BaseModel):
    type: Literal["signature", "initial", "name", "email", "date",
                  "text", "checkbox", "dropdown"]
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    required: bool = True
    label: Optional[str] = Field(default=None, max_length=120)
    recipient_email: Optional[EmailStr] = None


class CreateRequestIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=2000)
    sequential: bool = False
    recipients: List[RecipientIn] = Field(min_length=1, max_length=25)
    fields: List[FieldIn] = Field(min_length=1, max_length=200)
    source_version: Optional[int] = Field(default=None, ge=1)


class SelfSignField(BaseModel):
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    kind: Literal["signature", "text", "date", "check", "cross", "dot", "initial"] = "text"
    text: str = Field(default="", max_length=200)
    asset_id: Optional[str] = Field(default=None, max_length=32)


class SelfSignIn(BaseModel):
    placements: List[SelfSignField] = Field(min_length=1, max_length=100)
    source_version: Optional[int] = Field(default=None, ge=1)


class SubmitIn(BaseModel):
    values: Dict[str, str] = Field(default_factory=dict)


class DeclineIn(BaseModel):
    reason: str = Field(default="", max_length=500)


def _guard(action):
    try:
        return action()
    except signing.SigningError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _load_request(session, user, request_id: str) -> SignatureRequest:
    """Resolve a request only through workspace membership."""
    from docintel.db.models import WorkspaceMember

    row = session.execute(
        select(SignatureRequest)
        .join(WorkspaceMember,
              WorkspaceMember.workspace_id == SignatureRequest.workspace_id)
        .where(SignatureRequest.id == request_id,
               WorkspaceMember.user_id == user.id)
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


# ------------------------------------------------- saved signature assets

@router.get("/signatures")
def list_signatures(user: CurrentUser, session: DbSession) -> list:
    """Only ever the caller's own signatures."""
    assets = session.scalars(
        select(SignatureAsset).where(SignatureAsset.user_id == user.id)
        .order_by(SignatureAsset.created_at.desc())
    ).all()
    return [
        {"id": a.id, "label": a.label, "kind": a.kind,
         "width": a.width, "height": a.height, "is_default": a.is_default}
        for a in assets
    ]


@router.post("/signatures", status_code=status.HTTP_201_CREATED)
async def create_signature(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    label: str = Form("Signature"),
    kind: str = Form("drawn"),
    typed_name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """Create a saved signature by typing a name or uploading/drawing an image."""
    if kind == "typed":
        if not typed_name.strip():
            raise HTTPException(status_code=400, detail="A name is required.")
        png = _guard(lambda: signing.render_typed_signature(typed_name.strip()))
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="An image is required.")
        raw = await file.read(4 * 1024 * 1024)
        await file.close()
        if not raw:
            raise HTTPException(status_code=400, detail="The image is empty.")
        png, _, _ = _guard(lambda: signing.normalise_signature_image(raw))

    from PIL import Image
    import io as _io
    image = Image.open(_io.BytesIO(png))

    asset = SignatureAsset(
        user_id=user.id,
        label=(label or "Signature")[:80],
        kind="typed" if kind == "typed" else "drawn",
        storage_key="",
        width=image.width,
        height=image.height,
    )
    session.add(asset)
    session.flush()

    # Namespaced under the user, never under a workspace, and never served
    # from a public URL.
    key = f"signatures/{user.id}/{asset.id}.png"
    storage.put(key, png)
    asset.storage_key = key

    # Deliberately records that a signature was created, not what it looks
    # like. Signature imagery never enters a log.
    audit.record(session, action="signature.asset_created", actor=user,
                 detail=f"kind={asset.kind}", ip_address=client_ip(request))
    session.commit()

    return {"id": asset.id, "label": asset.label, "kind": asset.kind,
            "width": asset.width, "height": asset.height}


@router.get("/signatures/{asset_id}/image")
def signature_image(asset_id: str, user: CurrentUser, session: DbSession) -> Response:
    asset = session.scalar(
        select(SignatureAsset).where(
            SignatureAsset.id == asset_id,
            # Ownership is part of the query: one user can never fetch
            # another's signature, even with a valid id.
            SignatureAsset.user_id == user.id,
        )
    )
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return Response(
        content=storage.get(asset.storage_key),
        media_type="image/png",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/signatures/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_signature(asset_id: str, user: CurrentUser, session: DbSession) -> None:
    asset = session.scalar(
        select(SignatureAsset).where(
            SignatureAsset.id == asset_id, SignatureAsset.user_id == user.id,
        )
    )
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        storage.delete(asset.storage_key)
    except Exception:
        pass
    session.delete(asset)
    session.commit()


# ------------------------------------------------------------ fill & sign

@router.post("/documents/{document_id}/sign/self")
def self_sign(document_id: str, body: SelfSignIn, request: Request,
              user: CurrentUser, session: DbSession) -> dict:
    """Fill & Sign: the caller signs the document themselves, immediately."""
    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    marks = {"check": "✓", "cross": "✗", "dot": "●"}
    placements = []

    for item in body.placements:
        image = None
        if item.asset_id:
            asset = session.scalar(
                select(SignatureAsset).where(
                    SignatureAsset.id == item.asset_id,
                    SignatureAsset.user_id == user.id,
                )
            )
            if asset is None:
                raise HTTPException(status_code=404, detail="Signature not found")
            image = storage.get(asset.storage_key)

        placements.append(signing.Placement(
            page=item.page, x=item.x, y=item.y,
            width=item.width, height=item.height,
            kind=item.kind,
            text=marks.get(item.kind, item.text),
            image=image,
        ))

    output = _guard(lambda: signing.stamp(data, placements))
    result = docsvc.add_version(session, document, output, "signed",
                                actor=user, action="signature.self_signed",
                                detail=f"{len(placements)} mark(s)")
    session.commit()

    return {
        "document_id": document.id,
        "version": result.version.version,
        "placements": len(placements),
        "legal_notice": signing.LEGAL_NOTICE,
    }


# ------------------------------------------------------- request workflow

@router.post("/documents/{document_id}/signature-requests",
             status_code=status.HTTP_201_CREATED)
def create_signature_request(document_id: str, body: CreateRequestIn,
                             request: Request, user: CurrentUser,
                             session: DbSession) -> dict:
    document = require_document(session, user, document_id, write=True)

    sig_request = _guard(lambda: signing.create_request(
        session, document, user,
        title=body.title, message=body.message, sequential=body.sequential,
        recipients=[r.model_dump() for r in body.recipients],
        fields=[f.model_dump() for f in body.fields],
        source_version=body.source_version,
    ))
    session.commit()
    session.refresh(sig_request)

    return _serialise(sig_request, include_tokens=True)


@router.post("/signature-requests/{request_id}/send")
def send_request(request_id: str, request: Request, user: CurrentUser,
                 session: DbSession) -> dict:
    sig_request = _load_request(session, user, request_id)
    _guard(lambda: signing.send(session, sig_request, user,
                                ip_address=client_ip(request)))
    session.commit()
    session.refresh(sig_request)
    return _serialise(sig_request, include_tokens=True)


@router.get("/signature-requests/{request_id}")
def get_request(request_id: str, user: CurrentUser, session: DbSession) -> dict:
    return _serialise(_load_request(session, user, request_id), include_tokens=True)


@router.get("/signature-requests/{request_id}/audit")
def get_audit_trail(request_id: str, user: CurrentUser, session: DbSession) -> dict:
    sig_request = _load_request(session, user, request_id)
    return {
        "request_id": sig_request.id,
        "document_hash": sig_request.document_hash,
        "state": sig_request.state.value,
        "events": signing.audit_trail(sig_request),
        "legal_notice": signing.LEGAL_NOTICE,
    }


@router.post("/signature-requests/{request_id}/cancel")
def cancel_request(request_id: str, request: Request, user: CurrentUser,
                   session: DbSession) -> dict:
    sig_request = _load_request(session, user, request_id)
    _guard(lambda: signing.transition(session, sig_request,
                                      SignatureRequestState.CANCELLED))
    signing.record_event(session, sig_request, "request.cancelled",
                         actor=user.email, ip_address=client_ip(request))
    session.commit()
    session.refresh(sig_request)
    return _serialise(sig_request)


@router.post("/signature-requests/{request_id}/finalise")
def finalise_request(request_id: str, user: CurrentUser, session: DbSession) -> dict:
    sig_request = _load_request(session, user, request_id)
    version = _guard(lambda: signing.finalise(session, sig_request, user))
    session.commit()
    return {"request_id": sig_request.id, "signed_version": version,
            "legal_notice": signing.LEGAL_NOTICE}


@router.get("/documents/{document_id}/signature-requests")
def list_for_document(document_id: str, user: CurrentUser, session: DbSession,
                      include_links: bool = False) -> list:
    """List this document's signature requests.

    Signing links are omitted by default so they are not sprayed through every
    listing, but the sender genuinely needs to re-copy them after a reload, so
    `include_links=true` returns them. The caller has already proven write
    access to the document to get this far.
    """
    document = require_document(session, user, document_id,
                                write=include_links)
    rows = session.scalars(
        select(SignatureRequest)
        .where(SignatureRequest.document_id == document.id)
        .order_by(SignatureRequest.created_at.desc())
    ).all()
    return [_serialise(r, include_tokens=include_links) for r in rows]


# ------------------------------------------------------- recipient side

@router.get("/sign/{token}")
def open_signing(token: str, request: Request, session: DbSession) -> dict:
    """Public: the token IS the credential. No account required."""
    result = _guard(lambda: signing.open_for_signing(
        session, token, ip_address=client_ip(request)))
    session.commit()
    return result


@router.get("/sign/{token}/document")
def recipient_document(token: str, session: DbSession) -> Response:
    recipient = _guard(lambda: signing.recipient_by_token(session, token))
    sig_request = recipient.request

    document = session.get(Document, sig_request.document_id)
    data = docsvc.read_version(session, document, sig_request.source_version)

    return Response(
        content=data, media_type="application/pdf",
        headers={"X-Content-Type-Options": "nosniff",
                 "Cache-Control": "private, no-store"},
    )


@router.post("/sign/{token}/submit")
def submit(token: str, body: SubmitIn, request: Request, session: DbSession) -> dict:
    result = _guard(lambda: signing.submit_signature(
        session, token, body.values, ip_address=client_ip(request)))
    session.commit()
    return {**result, "legal_notice": signing.LEGAL_NOTICE}


@router.post("/sign/{token}/decline")
def decline(token: str, body: DeclineIn, request: Request,
            session: DbSession) -> dict:
    result = _guard(lambda: signing.decline(
        session, token, body.reason, ip_address=client_ip(request)))
    session.commit()
    return result


# ------------------------------------------------------------- helpers

def _serialise(request: SignatureRequest, *, include_tokens: bool = False) -> dict:
    return {
        "id": request.id,
        "document_id": request.document_id,
        "title": request.title,
        "message": request.message,
        "state": request.state.value,
        "sequential": request.sequential,
        "document_hash": request.document_hash,
        "source_version": request.source_version,
        "signed_version": request.signed_version,
        "recipients": [
            {
                "id": r.id, "email": r.email, "name": r.name,
                "order": r.order, "state": r.state.value,
                # Tokens are the signing credential, so they go only to the
                # sender, never in a list visible to anyone else.
                **({"signing_path": f"/sign/{r.access_token}"} if include_tokens else {}),
            }
            for r in request.recipients
        ],
        "fields": [
            {"id": f.id, "type": f.type.value, "page": f.page,
             "x": f.x, "y": f.y, "width": f.width, "height": f.height,
             "required": f.required, "label": f.label,
             "recipient_id": f.recipient_id, "filled": f.value is not None}
            for f in request.fields
        ],
        "legal_notice": signing.LEGAL_NOTICE,
    }
