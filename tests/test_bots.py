import lounge.events as events
from conftest import make_channel, make_workspace, setup_admin


def test_bot_create_shows_token_once(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    bot = client.post(
        f"/api/workspaces/{wid}/bots", json={"name": "Hermes"}, headers=headers
    ).json()
    assert "token" in bot and bot["is_bot"] is True
    token = bot["token"]

    # The token is never returned again by listing endpoints.
    bots = client.get(f"/api/workspaces/{wid}/bots", headers=headers).json()
    assert all("token" not in b for b in bots)

    # ... but it authenticates.
    r = client.get("/api/users/@me", headers={"Authorization": f"LOUNGE-BOT {token}"})
    assert r.status_code == 200


def test_bot_provision_orchestration(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]

    res = client.post(
        f"/api/workspaces/{wid}/bots/provision",
        json={"name": "Scout", "watch_url": "http://hooks.test/scout"},
        headers=headers,
    ).json()

    # Everything created and ids returned.
    assert res["bot"]["is_bot"] is True
    assert "token" in res
    assert res["role"]["name"] == "Agent: Scout"
    assert res["category"]["name"] == "Scout"
    assert res["channel"]["is_private"] is True
    assert res["channel"]["allowed_role_ids"] == [res["role"]["id"]]
    assert res["channel"]["category_id"] == res["category"]["id"]
    assert res["role_assignment"] == {
        "user_id": res["bot"]["id"],
        "role_id": res["role"]["id"],
    }
    assert res["watch_hook"]["url"] == "http://hooks.test/scout"
    assert res["join"]["channel_id"] == res["channel"]["id"]

    # The bot actually has the role (so it can see its private channel).
    bot_token = res["token"]
    data = client.get(
        f"/api/workspaces/{wid}/channels",
        headers={"Authorization": f"LOUNGE-BOT {bot_token}"},
    ).json()
    assert res["channel"]["id"] in {c["id"] for c in data["channels"]}


def test_watch_hook_fires_on_message(client, monkeypatch):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    res = client.post(
        f"/api/workspaces/{wid}/bots/provision",
        json={"name": "Scout", "watch_url": "http://hooks.test/scout"},
        headers=headers,
    ).json()
    cid = res["channel"]["id"]
    bot_token = res["token"]

    calls = []

    async def fake_post(url, payload):
        calls.append((url, payload))

    monkeypatch.setattr(events, "http_post_json", fake_post)

    client.post(
        f"/api/channels/{cid}/messages",
        json={"content": "watch me"},
        headers={"Authorization": f"LOUNGE-BOT {bot_token}"},
    )

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "http://hooks.test/scout"
    assert payload["content"] == "watch me"
