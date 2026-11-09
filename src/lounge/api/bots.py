"""Bot accounts + the one-call workspace provisioning orchestration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import (
    BotToken,
    Category,
    Channel,
    MemberRole,
    Membership,
    Role,
    User,
    WatchHook,
    get_session,
)
from .common import get_workspace, slugify

router = APIRouter(prefix="/api/workspaces", tags=["bots"])


class BotBody(BaseModel):
    name: str


class ProvisionBody(BaseModel):
    name: str
    watch_url: str | None = None


def _unique_username(session: Session, base: str) -> str:
    candidate = slugify(base)
    n = 1
    while session.execute(
        select(User).where(User.username == candidate)
    ).scalar_one_or_none() is not None:
        n += 1
        candidate = f"{slugify(base)}-{n}"
    return candidate


def _create_bot(session: Session, workspace_id: int, name: str) -> tuple[User, str]:
    """Create a bot user + membership + token. Returns (user, raw_token)."""
    username = _unique_username(session, name)
    bot = User(
        username=username,
        password_hash=None,
        display_name=name,
        is_bot=True,
        is_admin=False,
    )
    session.add(bot)
    session.flush()
    session.add(Membership(workspace_id=workspace_id, user_id=bot.id))
    raw = auth.new_bot_token()
    session.add(BotToken(user_id=bot.id, token_hash=auth.hash_token(raw)))
    return bot, raw


@router.get("/{workspace_id}/bots")
def list_bots(
    workspace_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    auth.require_member(session, workspace_id, user)
    rows = session.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.workspace_id == workspace_id, User.is_bot.is_(True))
        .order_by(User.id)
    ).scalars().all()
    return [u.to_dict() for u in rows]


@router.post("/{workspace_id}/bots", status_code=201)
def create_bot(
    workspace_id: int,
    body: BotBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    bot, raw = _create_bot(session, workspace_id, body.name)
    session.commit()
    data = bot.to_dict()
    data["token"] = raw  # shown ONCE
    return data


@router.delete("/{workspace_id}/bots/{user_id}")
def remove_bot(
    workspace_id: int,
    user_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    bot = session.get(User, user_id)
    if bot is None or not bot.is_bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    import datetime as dt

    for token in session.execute(
        select(BotToken).where(
            BotToken.user_id == user_id, BotToken.revoked_at.is_(None)
        )
    ).scalars():
        token.revoked_at = dt.datetime.now(dt.timezone.utc)
    membership = session.get(Membership, (workspace_id, user_id))
    if membership is not None:
        session.delete(membership)
    session.execute(
        MemberRole.__table__.delete().where(
            MemberRole.workspace_id == workspace_id, MemberRole.user_id == user_id
        )
    )
    session.commit()
    return {"ok": True, "removed": user_id}


@router.post("/{workspace_id}/bots/provision", status_code=201)
def provision_bot(
    workspace_id: int,
    body: ProvisionBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    """One orchestrated call that mirrors discord-ops bot_workspace_provision.

    Order: bot user+token → role → category → private channel → role
    assignment → optional watch hook.
    """
    get_workspace(session, workspace_id)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    # 1. bot user + membership + token
    bot, raw = _create_bot(session, workspace_id, body.name)

    # 2. role "Agent: <name>"
    role = Role(
        workspace_id=workspace_id,
        name=f"Agent: {body.name}",
        color="#5865f2",
        can_manage_channels=False,
        can_moderate=False,
    )
    session.add(role)
    session.flush()

    # 3. category "<name>"
    cat_pos = session.query(Category).filter(
        Category.workspace_id == workspace_id
    ).count()
    category = Category(workspace_id=workspace_id, name=body.name, position=cat_pos)
    session.add(category)
    session.flush()

    # 4. private text channel "<slug>" scoped to the new role
    ch_pos = session.query(Channel).filter(
        Channel.workspace_id == workspace_id
    ).count()
    channel = Channel(
        workspace_id=workspace_id,
        category_id=category.id,
        name=slugify(body.name),
        kind="text",
        is_private=True,
        allowed_role_ids=[role.id],
        position=ch_pos,
    )
    session.add(channel)
    session.flush()

    # 5. assign role to the bot
    session.add(
        MemberRole(workspace_id=workspace_id, user_id=bot.id, role_id=role.id)
    )

    # 6. optional watch hook on the channel
    watch = None
    if body.watch_url:
        session.add(WatchHook(channel_id=channel.id, url=body.watch_url))
        watch = {"channel_id": channel.id, "url": body.watch_url}

    session.commit()

    return {
        "bot": bot.to_dict(),
        "token": raw,  # shown ONCE
        "role": role.to_dict(),
        "category": category.to_dict(),
        "channel": channel.to_dict(),
        "role_assignment": {"user_id": bot.id, "role_id": role.id},
        "watch_hook": watch,
        "join": {
            "workspace_id": workspace_id,
            "channel_id": channel.id,
            "ws_url": f"/api/ws?wid={workspace_id}&token=<bot-token>",
        },
    }
