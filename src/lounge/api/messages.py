"""Messages (channel-level; threaded messages live in threads.py)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, events
from ..db import Message, User, WatchHook, get_session
from .common import get_channel, get_message

router = APIRouter(prefix="/api", tags=["messages"])


class MessageBody(BaseModel):
    content: str


class MessagePatch(BaseModel):
    content: str


def _require_channel_access(session: Session, channel_id: int, user: User):
    channel = get_channel(session, channel_id)
    perms = auth.require_member(session, channel.workspace_id, user)
    if not auth.can_view_channel(perms, channel):
        raise HTTPException(status_code=403, detail="Channel not accessible")
    return channel, perms


async def dispatch_message_create(session: Session, channel, message: Message):
    """Broadcast a newly created message and fire the channel watch hook."""
    payload = {
        "type": "message.create",
        "channel_id": channel.id,
        "thread_id": message.thread_id,
        "message": message.to_dict(),
    }
    await events.hub.broadcast(channel.workspace_id, payload)
    hook = session.get(WatchHook, channel.id)
    if hook is not None:
        await events.deliver_watch_hook(hook.url, payload["message"])


@router.get("/channels/{channel_id}/messages")
def list_messages(
    channel_id: int,
    before: int | None = Query(default=None),
    limit: int = Query(default=50, le=100, ge=1),
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    channel, _ = _require_channel_access(session, channel_id, user)
    stmt = select(Message).where(
        Message.channel_id == channel_id, Message.thread_id.is_(None)
    )
    if before is not None:
        stmt = stmt.where(Message.id < before)
    stmt = stmt.order_by(Message.id.desc()).limit(limit)
    rows = session.execute(stmt).scalars().all()
    return [m.to_dict() for m in rows]


@router.post("/channels/{channel_id}/messages", status_code=201)
async def create_message(
    channel_id: int,
    body: MessageBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    channel, _ = _require_channel_access(session, channel_id, user)
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Message content required")
    message = Message(
        channel_id=channel_id,
        thread_id=None,
        author_id=user.id,
        content=body.content,
    )
    session.add(message)
    session.commit()
    await dispatch_message_create(session, channel, message)
    return message.to_dict()


@router.patch("/messages/{message_id}")
async def edit_message(
    message_id: int,
    body: MessagePatch,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    message = get_message(session, message_id)
    if message.author_id != user.id:
        raise HTTPException(status_code=403, detail="Only the author can edit")
    message.content = body.content
    message.edited_at = dt.datetime.now(dt.timezone.utc)
    session.commit()
    channel = get_channel(session, message.channel_id)
    payload = {
        "type": "message.edit",
        "channel_id": channel.id,
        "thread_id": message.thread_id,
        "message": message.to_dict(),
    }
    await events.hub.broadcast(channel.workspace_id, payload)
    return message.to_dict()


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    message = get_message(session, message_id)
    channel = get_channel(session, message.channel_id)
    perms = auth.get_perms(session, channel.workspace_id, user)
    if message.author_id != user.id and not perms.can_moderate:
        raise HTTPException(status_code=403, detail="Not allowed to delete")
    thread_id = message.thread_id
    session.delete(message)
    session.commit()
    await events.hub.broadcast(
        channel.workspace_id,
        {
            "type": "message.delete",
            "channel_id": channel.id,
            "thread_id": thread_id,
            "message_id": message_id,
        },
    )
    return {"ok": True, "deleted": message_id}
