"""Roles + member-role assignment."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import MemberRole, Membership, Role, User, get_session
from .common import get_workspace

router = APIRouter(prefix="/api", tags=["roles"])


class RoleBody(BaseModel):
    name: str
    color: str = "#99aab5"
    can_manage_channels: bool = False
    can_moderate: bool = False


class RolePatch(BaseModel):
    name: str | None = None
    color: str | None = None
    can_manage_channels: bool | None = None
    can_moderate: bool | None = None


class RoleIdsBody(BaseModel):
    role_ids: list[int]


def _require_moderator(session: Session, workspace_id: int, user: User):
    perms = auth.require_member(session, workspace_id, user)
    if not perms.can_moderate:
        raise HTTPException(status_code=403, detail="Requires moderation permission")
    return perms


def _get_role(session: Session, role_id: int) -> Role:
    role = session.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.get("/workspaces/{workspace_id}/roles")
def list_roles(
    workspace_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    auth.require_member(session, workspace_id, user)
    rows = session.execute(
        select(Role).where(Role.workspace_id == workspace_id).order_by(Role.id)
    ).scalars().all()
    return [r.to_dict() for r in rows]


@router.post("/workspaces/{workspace_id}/roles", status_code=201)
def create_role(
    workspace_id: int,
    body: RoleBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    _require_moderator(session, workspace_id, user)
    role = Role(
        workspace_id=workspace_id,
        name=body.name,
        color=body.color,
        can_manage_channels=body.can_manage_channels,
        can_moderate=body.can_moderate,
    )
    session.add(role)
    session.commit()
    return role.to_dict()


@router.patch("/roles/{role_id}")
def update_role(
    role_id: int,
    body: RolePatch,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    role = _get_role(session, role_id)
    _require_moderator(session, role.workspace_id, user)
    if body.name is not None:
        role.name = body.name
    if body.color is not None:
        role.color = body.color
    if body.can_manage_channels is not None:
        role.can_manage_channels = body.can_manage_channels
    if body.can_moderate is not None:
        role.can_moderate = body.can_moderate
    session.commit()
    return role.to_dict()


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    role = _get_role(session, role_id)
    _require_moderator(session, role.workspace_id, user)
    session.execute(
        MemberRole.__table__.delete().where(MemberRole.role_id == role_id)
    )
    session.delete(role)
    session.commit()
    return {"ok": True, "deleted": role_id}


def _ensure_member(session: Session, workspace_id: int, user_id: int):
    """Ensure the target user is a workspace member (assigning a role joins them).

    v1 has no invite-link flow, so a moderator/admin assigning a workspace role
    is the supported way to add a member.
    """
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if session.get(Membership, (workspace_id, user_id)) is None:
        session.add(Membership(workspace_id=workspace_id, user_id=user_id))
        session.flush()


@router.put("/workspaces/{workspace_id}/members/{user_id}/roles")
def set_member_roles(
    workspace_id: int,
    user_id: int,
    body: RoleIdsBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    _require_moderator(session, workspace_id, user)
    _ensure_member(session, workspace_id, user_id)
    valid = {
        r
        for (r,) in session.execute(
            select(Role.id).where(
                Role.workspace_id == workspace_id, Role.id.in_(body.role_ids)
            )
        ).all()
    }
    unknown = set(body.role_ids) - valid
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown role ids: {sorted(unknown)}")
    session.execute(
        MemberRole.__table__.delete().where(
            MemberRole.workspace_id == workspace_id,
            MemberRole.user_id == user_id,
        )
    )
    for rid in valid:
        session.add(MemberRole(workspace_id=workspace_id, user_id=user_id, role_id=rid))
    session.commit()
    return {"user_id": user_id, "role_ids": sorted(valid)}


@router.post("/workspaces/{workspace_id}/members/{user_id}/roles/{role_id}")
def add_member_role(
    workspace_id: int,
    user_id: int,
    role_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    _require_moderator(session, workspace_id, user)
    _ensure_member(session, workspace_id, user_id)
    role = _get_role(session, role_id)
    if role.workspace_id != workspace_id:
        raise HTTPException(status_code=422, detail="Role is not in this workspace")
    if session.get(MemberRole, (workspace_id, user_id, role_id)) is None:
        session.add(
            MemberRole(workspace_id=workspace_id, user_id=user_id, role_id=role_id)
        )
        session.commit()
    return {"ok": True, "user_id": user_id, "role_id": role_id}


@router.delete("/workspaces/{workspace_id}/members/{user_id}/roles/{role_id}")
def remove_member_role(
    workspace_id: int,
    user_id: int,
    role_id: int,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    get_workspace(session, workspace_id)
    _require_moderator(session, workspace_id, user)
    link = session.get(MemberRole, (workspace_id, user_id, role_id))
    if link is not None:
        session.delete(link)
        session.commit()
    return {"ok": True, "user_id": user_id, "role_id": role_id}
