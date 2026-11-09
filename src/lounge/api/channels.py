"""Channels + categories."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth, events
from ..db import Category, Channel, Thread, User, get_session
from .common import get_channel, get_workspace

router = APIRouter(prefix="/api", tags=["channels"])


class CategoryBody(BaseModel):
    name: str


class ChannelBody(BaseModel):
    name: str
    kind: str = "text"
    category_id: int | None = None
    is_private: bool = False
    allowed_role_ids: list[int] = []


class ChannelPatch(BaseModel):
    name: str | None = None
    is_private: bool | None = None
    allowed_role_ids: list[int] | None = None
    category_id: int | None = None


def _thread_counts(session: Session, channel_ids: list[int]) -> dict[int, int]:
    if not channel_ids:
        return {}
    rows = session.execute(
        select(Thread.channel_id, func.count())
        .where(Thread.channel_id.in_(channel_ids), Thread.archived.is_(False))
        .group_by(Thread.channel_id)
    ).all()
    return {cid: count for cid, count in rows}


@router.get("/workspaces/{workspace_id}/channels")
def list_channels(
    workspace_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    perms = auth.require_member(session, workspace_id, user)
    categories = session.execute(
        select(Category)
        .where(Category.workspace_id == workspace_id)
        .order_by(Category.position, Category.id)
    ).scalars().all()
    channels = session.execute(
        select(Channel)
        .where(Channel.workspace_id == workspace_id)
        .order_by(Channel.position, Channel.id)
    ).scalars().all()
    visible = [c for c in channels if auth.can_view_channel(perms, c)]
    counts = _thread_counts(session, [c.id for c in visible])
    channel_dicts = []
    for c in visible:
        d = c.to_dict()
        d["thread_count"] = counts.get(c.id, 0)
        channel_dicts.append(d)
    return {
        "categories": [c.to_dict() for c in categories],
        "channels": channel_dicts,
    }


@router.post("/workspaces/{workspace_id}/categories", status_code=201)
def create_category(
    workspace_id: int,
    body: CategoryBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    perms = auth.require_member(session, workspace_id, user)
    if not perms.can_manage_channels:
        raise HTTPException(status_code=403, detail="Cannot manage channels")
    pos = session.execute(
        select(func.count()).select_from(Category).where(
            Category.workspace_id == workspace_id
        )
    ).scalar_one()
    cat = Category(workspace_id=workspace_id, name=body.name, position=pos)
    session.add(cat)
    session.commit()
    return cat.to_dict()


@router.post("/workspaces/{workspace_id}/channels", status_code=201)
async def create_channel(
    workspace_id: int,
    body: ChannelBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    perms = auth.require_member(session, workspace_id, user)
    if not perms.can_manage_channels:
        raise HTTPException(status_code=403, detail="Cannot manage channels")
    if body.kind != "text":
        raise HTTPException(status_code=422, detail="Only text channels in v1")
    pos = session.execute(
        select(func.count()).select_from(Channel).where(
            Channel.workspace_id == workspace_id
        )
    ).scalar_one()
    channel = Channel(
        workspace_id=workspace_id,
        category_id=body.category_id,
        name=body.name,
        kind="text",
        is_private=body.is_private,
        allowed_role_ids=list(body.allowed_role_ids or []),
        position=pos,
    )
    session.add(channel)
    session.commit()
    payload = channel.to_dict()
    await events.hub.broadcast(
        workspace_id, {"type": "channel.create", "channel": payload}
    )
    return payload


@router.patch("/channels/{channel_id}")
async def update_channel(
    channel_id: int,
    body: ChannelPatch,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    channel = get_channel(session, channel_id)
    perms = auth.require_member(session, channel.workspace_id, user)
    if not perms.can_manage_channels:
        raise HTTPException(status_code=403, detail="Cannot manage channels")
    if body.name is not None:
        channel.name = body.name
    if body.is_private is not None:
        channel.is_private = body.is_private
    if body.allowed_role_ids is not None:
        channel.allowed_role_ids = list(body.allowed_role_ids)
    if body.category_id is not None:
        channel.category_id = body.category_id
    session.commit()
    payload = channel.to_dict()
    await events.hub.broadcast(
        channel.workspace_id, {"type": "channel.update", "channel": payload}
    )
    return payload


@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    channel = get_channel(session, channel_id)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    workspace_id = channel.workspace_id
    session.delete(channel)
    session.commit()
    await events.hub.broadcast(
        workspace_id, {"type": "channel.delete", "channel_id": channel_id}
    )
    return {"ok": True, "deleted": channel_id}
