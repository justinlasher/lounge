"""Workspaces + membership listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, events
from ..db import MemberRole, Membership, Role, User, Workspace, get_session
from .common import get_workspace

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceBody(BaseModel):
    name: str


@router.get("")
def list_workspaces(
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    if user.is_admin:
        rows = session.execute(select(Workspace).order_by(Workspace.id)).scalars().all()
    else:
        rows = (
            session.execute(
                select(Workspace)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(Membership.user_id == user.id)
                .order_by(Workspace.id)
            )
            .scalars()
            .all()
        )
    return [w.to_dict() for w in rows]


@router.post("", status_code=201)
def create_workspace(
    body: WorkspaceBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    ws = Workspace(name=body.name)
    session.add(ws)
    session.flush()
    # Built-in default "Member" role + creator membership.
    session.add(Role(workspace_id=ws.id, name="Member", color="#99aab5"))
    session.add(Membership(workspace_id=ws.id, user_id=user.id))
    session.commit()
    return ws.to_dict()


@router.get("/{workspace_id}/members")
def list_members(
    workspace_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    auth.require_member(session, workspace_id, user)
    members = session.execute(
        select(Membership).where(Membership.workspace_id == workspace_id)
    ).scalars().all()
    online = set(events.hub.online_users(workspace_id))
    out = []
    for m in members:
        u = session.get(User, m.user_id)
        if u is None:
            continue
        role_ids = [
            r
            for (r,) in session.execute(
                select(MemberRole.role_id).where(
                    MemberRole.workspace_id == workspace_id,
                    MemberRole.user_id == m.user_id,
                )
            ).all()
        ]
        data = u.to_dict()
        data["role_ids"] = role_ids
        data["online"] = u.id in online
        out.append(data)
    return out


@router.post("/{workspace_id}/members/{user_id}")
def add_member(
    workspace_id: int,
    user_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    """Add an existing user to a workspace (admin only)."""
    get_workspace(session, workspace_id)
    auth.require_member(session, workspace_id, user)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    existing = session.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Already a member")
    membership = Membership(workspace_id=workspace_id, user_id=user_id)
    session.add(membership)
    session.commit()
    events.hub.broadcast(
        workspace_id,
        {"type": "member.add", "user_id": user_id, "username": target.username},
    )
    return target.to_dict()
