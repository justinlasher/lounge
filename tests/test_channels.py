from conftest import (
    add_human,
    join_workspace,
    login,
    make_channel,
    make_workspace,
    setup_admin,
)


def test_channel_and_message_crud(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    cid = make_channel(client, headers, wid)["id"]

    m = client.post(
        f"/api/channels/{cid}/messages", json={"content": "hi"}, headers=headers
    ).json()
    assert m["content"] == "hi"

    msgs = client.get(f"/api/channels/{cid}/messages", headers=headers).json()
    assert len(msgs) == 1 and msgs[0]["id"] == m["id"]

    # edit
    e = client.patch(
        f"/api/messages/{m['id']}", json={"content": "edited"}, headers=headers
    ).json()
    assert e["content"] == "edited" and e["edited_at"]

    # delete
    assert client.delete(f"/api/messages/{m['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/channels/{cid}/messages", headers=headers).json() == []


def test_non_admin_cannot_create_channel(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    bob = add_human(client, headers, "bob")
    join_workspace(client, headers, wid, bob["id"])
    _, bob_h = login(client, "bob", "pw")

    r = client.post(
        f"/api/workspaces/{wid}/channels",
        json={"name": "secret", "kind": "text"},
        headers=bob_h,
    )
    assert r.status_code == 403


def test_non_moderator_cannot_delete_others_messages(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    cid = make_channel(client, headers, wid)["id"]
    bob = add_human(client, headers, "bob")
    join_workspace(client, headers, wid, bob["id"])
    _, bob_h = login(client, "bob", "pw")

    # admin posts a message; bob cannot delete it
    m = client.post(
        f"/api/channels/{cid}/messages", json={"content": "admin msg"}, headers=headers
    ).json()
    assert client.delete(f"/api/messages/{m['id']}", headers=bob_h).status_code == 403

    # bob can delete his own
    own = client.post(
        f"/api/channels/{cid}/messages", json={"content": "mine"}, headers=bob_h
    ).json()
    assert client.delete(f"/api/messages/{own['id']}", headers=bob_h).status_code == 200


def test_private_channel_hidden_from_unauthorized_member(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    # role that is NOT granted to bob
    role = client.post(
        f"/api/workspaces/{wid}/roles", json={"name": "Insiders"}, headers=headers
    ).json()
    make_channel(
        client, headers, wid, name="secret", is_private=True, allowed_role_ids=[role["id"]]
    )
    public = make_channel(client, headers, wid, name="general")

    bob = add_human(client, headers, "bob")
    join_workspace(client, headers, wid, bob["id"])
    _, bob_h = login(client, "bob", "pw")

    data = client.get(f"/api/workspaces/{wid}/channels", headers=bob_h).json()
    names = {c["name"] for c in data["channels"]}
    assert "general" in names
    assert "secret" not in names

    # admin still sees it
    admin_data = client.get(f"/api/workspaces/{wid}/channels", headers=headers).json()
    assert "secret" in {c["name"] for c in admin_data["channels"]}
