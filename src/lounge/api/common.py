"""Shared lookup + serialization helpers for the routers."""

from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..db import Channel, Message, Thread, Workspace


def get_workspace(session: Session, workspace_id: int) -> Workspace:
    ws = session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


def get_channel(session: Session, channel_id: int) -> Channel:
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


def get_message(session: Session, message_id: int) -> Message:
    message = session.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


def get_thread(session: Session, thread_id: int) -> Thread:
    thread = session.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "channel"
