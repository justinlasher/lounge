from conftest import setup_admin, make_workspace


def test_setup_and_login_flow(client):
    token, headers = setup_admin(client)
    me = client.get("/api/users/@me", headers=headers).json()
    assert me["username"] == "admin"
    assert me["is_admin"] is True

    # login again returns a working token
    r = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "admin"


def test_setup_only_once(client):
    setup_admin(client)
    r = client.post(
        "/api/setup",
        json={"username": "two", "password": "pw", "setup_token": "test-setup-token"},
    )
    assert r.status_code == 409


def test_setup_bad_token(client):
    r = client.post(
        "/api/setup",
        json={"username": "admin", "password": "pw", "setup_token": "wrong"},
    )
    assert r.status_code == 403


def test_bad_credentials(client):
    setup_admin(client)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_auth(client):
    setup_admin(client)
    client.cookies.clear()  # drop the session cookie set during setup
    assert client.get("/api/users/@me").status_code == 401


def test_bot_token_auth_on_rest(client):
    token, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    bot = client.post(
        f"/api/workspaces/{wid}/bots", json={"name": "Hermes"}, headers=headers
    ).json()
    bot_token = bot["token"]
    # Bot authenticates with the LOUNGE-BOT scheme.
    r = client.get(
        "/api/users/@me", headers={"Authorization": f"LOUNGE-BOT {bot_token}"}
    )
    assert r.status_code == 200
    assert r.json()["is_bot"] is True
