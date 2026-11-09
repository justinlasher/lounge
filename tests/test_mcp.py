from lounge import mcp_server
from conftest import make_workspace, setup_admin


def mcp_client(client, token, wid):
    # Starlette's TestClient is an httpx.Client that routes in-process to the
    # ASGI app, so we can hand it straight to the MCP REST client.
    return mcp_server.LoungeClient(token=token, workspace_id=wid, client=client)


def test_workspace_overview_and_channel_tools(client):
    token, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    mc = mcp_client(client, token, wid)

    created = mcp_server._channel_create(mc, "general")
    assert created["name"] == "general"

    overview = mcp_server._workspace_overview(mc)
    assert overview["counts"]["channels"] == 1
    assert overview["workspace_id"] == wid

    info = mcp_server._channel_info(mc, created["id"])
    assert info["id"] == created["id"]


def test_confirm_gate_makes_zero_rest_calls(client, monkeypatch):
    token, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    mc = mcp_client(client, token, wid)

    calls = []
    real_request = mc.request

    def spy(method, path, **kw):
        calls.append((method, path))
        return real_request(method, path, **kw)

    monkeypatch.setattr(mc, "request", spy)

    assert mcp_server._channel_delete(mc, 123, confirm=False) == {
        "would_do": ["DELETE channel 123"]
    }
    assert mcp_server._message_delete(mc, 1, 5, confirm=False) == {
        "would_do": ["DELETE message 5 in 1"]
    }
    assert mcp_server._bot_remove(mc, 9, confirm=False) == {
        "would_do": ["REMOVE bot user 9"]
    }
    assert mcp_server._role_delete(mc, 2, confirm=False) == {
        "would_do": ["DELETE role 2"]
    }
    assert calls == []  # zero REST calls while unconfirmed


def test_error_mapping_is_clean(client):
    token, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    mc = mcp_client(client, token, wid)

    # 404 maps to a clean message, no token leakage.
    res = mcp_server._message_edit(mc, 999999, "nope")
    assert "error" in res
    assert "Not found" in res["error"]
    assert token not in res["error"]


def test_bot_provision_shape_via_mcp(client):
    token, headers = setup_admin(client)
    wid = make_workspace(client, headers)["id"]
    mc = mcp_client(client, token, wid)

    res = mcp_server._bot_provision(mc, "Scout", watch_url="http://hooks.test/x")
    assert res["role"]["name"] == "Agent: Scout"
    assert res["category"]["name"] == "Scout"
    assert res["channel"]["is_private"] is True
    assert res["channel"]["allowed_role_ids"] == [res["role"]["id"]]
    assert res["role_assignment"]["role_id"] == res["role"]["id"]


def test_tool_registration_builds():
    # The FastMCP server assembles without error (all tools register).
    server = mcp_server.build_server()
    assert server is not None
