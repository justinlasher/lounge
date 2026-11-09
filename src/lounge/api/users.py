"""User accounts, setup bootstrap, and login."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth
from ..db import User, get_session

router = APIRouter(prefix="/api", tags=["users"])


class SetupBody(BaseModel):
    username: str
    password: str
    setup_token: str


class LoginBody(BaseModel):
    username: str
    password: str


class CreateUserBody(BaseModel):
    username: str
    password: str
    display_name: str | None = None


def _count_users(session: Session) -> int:
    return session.execute(select(func.count()).select_from(User)).scalar_one()


@router.post("/setup")
def setup(body: SetupBody, response: Response, session: Session = Depends(get_session)):
    if _count_users(session) > 0:
        raise HTTPException(status_code=409, detail="Setup already completed")
    auth.require_setup_token(body.setup_token)
    user = User(
        username=body.username,
        password_hash=auth.hash_password(body.password),
        display_name=body.username,
        is_bot=False,
        is_admin=True,
    )
    session.add(user)
    session.commit()
    token = auth.create_jwt(user.id)
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
    )
    return {"token": token, "user": user.to_dict()}


@router.post("/auth/login")
def login(body: LoginBody, response: Response, session: Session = Depends(get_session)):
    user = session.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()
    if user is None or not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = auth.create_jwt(user.id)
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
    )
    return {"token": token, "user": user.to_dict()}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@router.get("/users/@me")
def me(user: User = Depends(auth.get_current_user)):
    return user.to_dict()


@router.get("/users")
def list_users(
    user: User = Depends(auth.get_current_user), session: Session = Depends(get_session)
):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    rows = session.execute(select(User).order_by(User.id)).scalars().all()
    return [u.to_dict() for u in rows]


@router.post("/users", status_code=201)
def create_user(
    body: CreateUserBody,
    user: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    exists = session.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="Username already taken")
    new_user = User(
        username=body.username,
        password_hash=auth.hash_password(body.password),
        display_name=body.display_name or body.username,
        is_bot=False,
        is_admin=False,
    )
    session.add(new_user)
    session.commit()
    return new_user.to_dict()


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    _: User = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    return target.to_dict()
