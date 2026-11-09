"""Threads + threaded messages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, events
from ..db import Message, Thread, User, get_session
from .common import get_channel, get_thread
from .messages import _require_channel_access, dispatch_message_create

router = APIRouter(prefix="/api", tags=["threads"])


class ThreadBody(BaseModel):
    name: str
    root_message_id: int | None = None


class ThreadMessageBody(BaseModel):
    content: str


@router.get("/channels/{channel_id}/threads")
def list_threads(
    channel_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    _require_channel_access(session, channel_id, user)
    rows = session.execute(
        select(Thread)
        .where(Thread.channel_id == channel_id)
        .order_by(Thread.id.desc())
    ).scalars().all()
    return [t.to_dict() for t in rows]


@router.post("/channels/{channel_id}/threads", status_code=201)
async def create_thread(
    channel_id: int,
    body: ThreadBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    channel, _ = _require_channel_access(session, channel_id, user)
    if body.root_message_id is not None:
        root = session.get(Message, body.root_message_id)
        if root is None or root.channel_id != channel_id:
            raise HTTPException(status_code=404, detail="Root message not found")
    thread = Thread(
        channel_id=channel_id,
        root_message_id=body.root_message_id,
        title=body.name,
        created_by=user.id,
    )
    session.add(thread)
    session.commit()
    payload = thread.to_dict()
    await events.hub.broadcast(
        channel.workspace_id, {"type": "thread.create", "thread": payload}
    )
    return payload


@router.get("/threads/{thread_id}/messages")
def list_thread_messages(
    thread_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    thread = get_thread(session, thread_id)
    _require_channel_access(session, thread.channel_id, user)
    rows = session.execute(
        select(Message)
        .where(Message.thread_id == thread_id)
        .order_by(Message.id.asc())
    ).scalars().all()
    return [m.to_dict() for m in rows]


@router.post("/threads/{thread_id}/messages", status_code=201)
async def create_thread_message(
    thread_id: int,
    body: ThreadMessageBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    thread = get_thread(session, thread_id)
    channel, _ = _require_channel_access(session, thread.channel_id, user)
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Message content required")
    message = Message(
        channel_id=thread.channel_id,
        thread_id=thread_id,
        author_id=user.id,
        content=body.content,
    )
    session.add(message)
    session.commit()
    await dispatch_message_create(session, channel, message)
    return message.to_dict()
