"""Authentication & authorization helpers.

- bcrypt password hashing
- JWT issue/verify (web cookie + ``Bearer`` tokens)
- bot tokens via the ``LOUNGE-BOT <token>`` Authorization scheme
- ``get_current_user`` FastAPI dependency
- workspace permission resolution
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import (
    BotToken,
    MemberRole,
    Membership,
    Role,
    User,
    get_session,
)

JWT_ALGO = "HS256"
COOKIE_NAME = "lounge_token"
TOKEN_TTL = dt.timedelta(days=30)


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_jwt(user_id: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": str(user_id), "iat": int(now.timestamp()), "exp": int((now + TOKEN_TTL).timestamp())}
    return jwt.encode(payload, config.get_secret(), algorithm=JWT_ALGO)


def decode_jwt(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, config.get_secret(), algorithms=[JWT_ALGO])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Bot tokens
# --------------------------------------------------------------------------- #
def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_bot_token() -> str:
    return secrets.token_hex(24)


def resolve_bot_token(session: Session, raw: str) -> Optional[User]:
    digest = hash_token(raw)
    row = session.execute(
        select(BotToken).where(
            BotToken.token_hash == digest, BotToken.revoked_at.is_(None)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return session.get(User, row.user_id)


def resolve_token(session: Session, raw: str) -> Optional[User]:
    """Resolve an opaque token (JWT first, then bot token) to a user."""
    if not raw:
        return None
    uid = decode_jwt(raw)
    if uid is not None:
        return session.get(User, uid)
    return resolve_bot_token(session, raw)


# --------------------------------------------------------------------------- #
# Setup token (first-run admin bootstrap)
# --------------------------------------------------------------------------- #
def current_setup_token() -> Optional[str]:
    return os.environ.get("LOUNGE_SETUP_TOKEN") or None


def require_setup_token(token: str) -> None:
    expected = current_setup_token()
    if not expected:
        raise HTTPException(status_code=400, detail="Setup is not enabled")
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid setup token")


# --------------------------------------------------------------------------- #
# Current-user dependency
# --------------------------------------------------------------------------- #
def _extract_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization")
    if header:
        parts = header.split(" ", 1)
        if len(parts) == 2:
            scheme, value = parts[0].strip(), parts[1].strip()
            if scheme.lower() in ("bearer", "lounge-bot"):
                return value
    return request.cookies.get(COOKIE_NAME)


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> User:
    token = _extract_token(request)
    user = resolve_token(session, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def get_optional_user(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    token = _extract_token(request)
    return resolve_token(session, token) if token else None


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #
@dataclass
class Perms:
    is_admin: bool
    is_member: bool
    can_manage_channels: bool
    can_moderate: bool
    role_ids: list[int]


def get_perms(session: Session, workspace_id: int, user: User) -> Perms:
    role_ids = [
        r
        for (r,) in session.execute(
            select(MemberRole.role_id).where(
                MemberRole.workspace_id == workspace_id,
                MemberRole.user_id == user.id,
            )
        ).all()
    ]
    is_member = (
        session.get(Membership, (workspace_id, user.id)) is not None
    )
    manage = moderate = False
    if role_ids:
        for role in session.execute(
            select(Role).where(Role.id.in_(role_ids))
        ).scalars():
            manage = manage or role.can_manage_channels
            moderate = moderate or role.can_moderate
    if user.is_admin:
        manage = moderate = True
    return Perms(
        is_admin=user.is_admin,
        is_member=is_member or user.is_admin,
        can_manage_channels=manage,
        can_moderate=moderate,
        role_ids=role_ids,
    )


def require_member(session: Session, workspace_id: int, user: User) -> Perms:
    perms = get_perms(session, workspace_id, user)
    if not perms.is_member:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return perms


def can_view_channel(perms: Perms, channel) -> bool:
    if perms.is_admin:
        return True
    if not channel.is_private:
        return True
    allowed = set(channel.allowed_role_ids or [])
    return bool(allowed.intersection(perms.role_ids))
