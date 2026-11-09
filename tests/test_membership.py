"""Admin adds existing users to a workspace (regression: no join endpoint existed)."""

from conftest import setup_admin


def _mk_user(ac, admin_h, name):
    r = ac.post("/api/users", json={"username": name, "password": "pw-123456"},
                headers=admin_h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mk_workspace(ac, admin_h, name="ws"):
    r = ac.post("/api/workspaces", json={"name": name}, headers=admin_h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_admin_can_add_member(client):
    tok, h = setup_admin(client)
    wid = _mk_workspace(client, h)
    uid = _mk_user(client, h, "latecomer")
    r = client.post(f"/api/workspaces/{wid}/members/{uid}", headers=h)
    assert r.status_code in (200, 201), r.text
    members = client.get(f"/api/workspaces/{wid}/members", headers=h).json()
    assert uid in [m["id"] for m in members]


def test_add_member_twice_conflicts(client):
    tok, h = setup_admin(client)
    wid = _mk_workspace(client, h)
    uid = _mk_user(client, h, "onceonly")
    url = f"/api/workspaces/{wid}/members/{uid}"
    assert client.post(url, headers=h).status_code in (200, 201)
    assert client.post(url, headers=h).status_code == 409


def test_non_admin_cannot_add_member(client):
    tok, h = setup_admin(client)
    wid = _mk_workspace(client, h)
    uid = _mk_user(client, h, "plainuser")
    client.post(f"/api/workspaces/{wid}/members/{uid}", headers=h)
    r = client.post("/api/auth/login",
                    json={"username": "plainuser", "password": "pw-123456"})
    non_admin_h = {"Authorization": f"Bearer {r.json()['token']}"}
    other = _mk_user(client, h, "someoneelse")
    r = client.post(f"/api/workspaces/{wid}/members/{other}", headers=non_admin_h)
    assert r.status_code == 403
