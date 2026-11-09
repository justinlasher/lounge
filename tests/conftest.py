"""Pytest fixtures: isolated in-memory DB + TestClient per test."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("LOUNGE_SECRET", "test-secret-do-not-use-0123456789abcdef")
os.environ["LOUNGE_SETUP_TOKEN"] = "test-setup-token"

from lounge import db, events  # noqa: E402
from lounge.api.app import app  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    db.init_engine("sqlite://")  # fresh in-memory DB (StaticPool)
    events.hub._conns.clear()
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def setup_admin(client, username="admin", password="pw"):
    r = client.post(
        "/api/setup",
        json={"username": username, "password": password, "setup_token": "test-setup-token"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return token, {"Authorization": f"Bearer {token}"}


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return token, {"Authorization": f"Bearer {token}"}


def make_workspace(client, headers, name="HQ"):
    return client.post("/api/workspaces", json={"name": name}, headers=headers).json()


def make_channel(client, headers, wid, name="general", **kw):
    body = {"name": name, "kind": "text"}
    body.update(kw)
    return client.post(f"/api/workspaces/{wid}/channels", json=body, headers=headers).json()


def add_human(client, admin_headers, username, password="pw"):
    """Create a human user via admin, then add to no workspace (caller does)."""
    return client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=admin_headers,
    ).json()


def member_role_id(client, admin_headers, wid):
    roles = client.get(f"/api/workspaces/{wid}/roles", headers=admin_headers).json()
    for r in roles:
        if r["name"] == "Member":
            return r["id"]
    raise AssertionError("Member role missing")


def join_workspace(client, admin_headers, wid, user_id, role_id=None):
    """Add a user to a workspace by assigning a role (auto-creates membership)."""
    rid = role_id or member_role_id(client, admin_headers, wid)
    r = client.post(
        f"/api/workspaces/{wid}/members/{user_id}/roles/{rid}", headers=admin_headers
    )
    assert r.status_code == 200, r.text
