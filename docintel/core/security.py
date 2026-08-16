"""Password hashing and access tokens."""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from docintel.config import settings

ALGORITHM = "HS256"

# bcrypt silently truncates at 72 bytes; rejecting instead of truncating means
# two different long passwords can never collide into the same hash.
MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, ttl_minutes: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    ttl = ttl_minutes if ttl_minutes is not None else settings.access_token_ttl_minutes
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Return the subject, or raise TokenError."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],       # pinned: never trust the header's alg
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid token") from exc

    if payload.get("typ") != "access":
        raise TokenError("wrong token type")

    subject = payload.get("sub")
    if not subject:
        raise TokenError("token has no subject")
    return subject
