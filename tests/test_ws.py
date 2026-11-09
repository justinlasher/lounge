from conftest import (
    add_human,
    join_workspace,
    login,
    make_channel,
    make_workspace,
    setup_admin,
)


def recv_until(sock, type_, match=None, limit=12):
    """Read WS frames until one of the given type (and fields) arrives."""
    for _ in range(limit):
        evt = sock.receive_json()
        if evt["type"] == type_ and all(evt.get(k) == v for k, v in (match or {}).items()):
            return evt
    raise AssertionError(f"did not receive {type_} {match or ''}")


def test_ws_message_broadcast_and_presence(client):
    admin_token, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    cid = make_channel(client, headers, wid)["id"]

    bob = add_human(client, headers, "bob")
    join_workspace(client, headers, wid, bob["id"])
    bob_token, bob_h = login(client, "bob", "pw")

    with client.websocket_connect(f"/api/ws?wid={wid}&token={admin_token}") as a:
        hello_a = a.receive_json()
        assert hello_a["type"] == "hello"
        assert hello_a["user_id"] in hello_a["online"]  # presence list on hello

        with client.websocket_connect(f"/api/ws?wid={wid}&token={bob_token}") as b:
            hello_b = b.receive_json()
            assert hello_b["type"] == "hello"
            assert set(hello_b["online"]) >= {hello_a["user_id"], hello_b["user_id"]}

            # A sees B come online.
            pres = recv_until(a, "presence.update", match={"user_id": hello_b["user_id"]})
            assert pres["online"] is True

            # A sends a message via REST; B receives message.create over WS.
            client.post(
                f"/api/channels/{cid}/messages",
                json={"content": "hey bob"},
                headers=headers,
            )
            msg_evt = recv_until(b, "message.create")
            assert msg_evt["message"]["content"] == "hey bob"
            assert msg_evt["channel_id"] == cid

            # Typing relay: B emits typing.start, A receives it.
            b.send_json({"type": "typing.start", "channel_id": cid})
            typing = recv_until(a, "typing.start")
            assert typing["channel_id"] == cid
            assert typing["user_id"] == hello_b["user_id"]
