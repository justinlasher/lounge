from conftest import make_channel, make_workspace, setup_admin


def test_thread_create_reply_and_isolation(client):
    _, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    cid = make_channel(client, headers, wid)["id"]

    root = client.post(
        f"/api/channels/{cid}/messages", json={"content": "root"}, headers=headers
    ).json()

    thread = client.post(
        f"/api/channels/{cid}/threads",
        json={"name": "Discussion", "root_message_id": root["id"]},
        headers=headers,
    ).json()
    assert thread["title"] == "Discussion"
    assert thread["root_message_id"] == root["id"]

    reply = client.post(
        f"/api/threads/{thread['id']}/messages",
        json={"content": "in thread"},
        headers=headers,
    ).json()
    assert reply["thread_id"] == thread["id"]

    # thread listing
    threads = client.get(f"/api/channels/{cid}/threads", headers=headers).json()
    assert len(threads) == 1

    # thread messages list contains the reply
    tmsgs = client.get(f"/api/threads/{thread['id']}/messages", headers=headers).json()
    assert [m["content"] for m in tmsgs] == ["in thread"]

    # thread messages are EXCLUDED from the channel message list
    channel_msgs = client.get(f"/api/channels/{cid}/messages", headers=headers).json()
    assert [m["content"] for m in channel_msgs] == ["root"]
