"""``lounge-ops`` — FastMCP stdio server that drives the Lounge REST API.

Configuration (env):
  LOUNGE_URL           base URL (default http://127.0.0.1:8000)
  LOUNGE_TOKEN         JWT or bot token used for all calls
  LOUNGE_WORKSPACE_ID  optional; otherwise the first visible workspace is used

Destructive tools take ``confirm: bool = False`` and, when False, return
``{"would_do": [...]}`` performing **zero** REST calls.  Error responses never
leak tokens.
"""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Optional

import httpx

# The stdio server class is only needed to actually serve; the tool logic
# works without it.  Support both the MCP 1.x name (FastMCP) and the 2.x name
# (MCPServer) — both expose a compatible ``.tool()`` decorator and ``.run()``.
try:
    from mcp.server.mcpserver import MCPServer as _ServerClass  # MCP >= 2
except Exception:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP as _ServerClass  # MCP 1.x
    except Exception:
        _ServerClass = None  # type: ignore

DEFAULT_URL = "http://127.0.0.1:8000"


class LoungeError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _env_int(key: str) -> Optional[int]:
    val = os.environ.get(key)
    try:
        return int(val) if val else None
    except ValueError:
        return None


def _map_error(resp: httpx.Response) -> LoungeError:
    code = resp.status_code
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    labels = {
        400: "Bad request",
        401: "Unauthorized — check LOUNGE_TOKEN",
        403: "Forbidden — token lacks permission",
        404: "Not found",
        422: "Invalid request",
    }
    base = labels.get(code, f"HTTP {code}")
    if detail and code not in (401,):
        base = f"{base}: {detail}"
    return LoungeError(base, status=code)


class LoungeClient:
    """Thin REST client with token auth + single-workspace context."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        workspace_id: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LOUNGE_URL") or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("LOUNGE_TOKEN")
        self._workspace_id = workspace_id or _env_int("LOUNGE_WORKSPACE_ID")
        self._client = client  # injectable (e.g. ASGI transport for selfcheck/tests)

    def _auth_header(self) -> dict[str, str]:
        if not self.token:
            return {}
        # JWTs have two dots; bot tokens are opaque hex.
        scheme = "Bearer" if self.token.count(".") >= 2 else "LOUNGE-BOT"
        return {"Authorization": f"{scheme} {self.token}"}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers.update(self._auth_header())
        owned = self._client is None
        client = self._client or httpx.Client(base_url=self.base_url, timeout=10.0)
        try:
            resp = client.request(method, path, headers=headers, **kwargs)
        finally:
            if owned:
                client.close()
        if resp.status_code >= 400:
            raise _map_error(resp)
        if resp.content:
            try:
                return resp.json()
            except ValueError:
                return {}
        return {}

    def workspace_id(self) -> int:
        if self._workspace_id is not None:
            return self._workspace_id
        workspaces = self.request("GET", "/api/workspaces")
        if not workspaces:
            raise LoungeError("No workspace available for this token")
        self._workspace_id = workspaces[0]["id"]
        return self._workspace_id


def _safe(fn):
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except LoungeError as exc:
            return {"error": str(exc)}
    return wrapper


# --------------------------------------------------------------------------- #
# Tool logic (client passed explicitly so selfcheck/tests can call directly)
# --------------------------------------------------------------------------- #
@_safe
def _workspace_overview(client: LoungeClient) -> dict:
    wid = client.workspace_id()
    data = client.request("GET", f"/api/workspaces/{wid}/channels")
    members = client.request("GET", f"/api/workspaces/{wid}/members")
    return {
        "workspace_id": wid,
        "categories": data["categories"],
        "channels": data["channels"],
        "counts": {
            "categories": len(data["categories"]),
            "channels": len(data["channels"]),
            "members": len(members),
        },
    }


@_safe
def _channel_list(client: LoungeClient) -> dict:
    wid = client.workspace_id()
    data = client.request("GET", f"/api/workspaces/{wid}/channels")
    return {"channels": data["channels"], "categories": data["categories"]}


@_safe
def _channel_info(client: LoungeClient, channel_id: int) -> dict:
    wid = client.workspace_id()
    data = client.request("GET", f"/api/workspaces/{wid}/channels")
    for ch in data["channels"]:
        if ch["id"] == channel_id:
            return ch
    raise LoungeError("Not found", status=404)


@_safe
def _channel_create(client: LoungeClient, name: str, category_id: int | None = None) -> dict:
    wid = client.workspace_id()
    return client.request(
        "POST",
        f"/api/workspaces/{wid}/channels",
        json={"name": name, "kind": "text", "category_id": category_id},
    )


@_safe
def _channel_update(
    client: LoungeClient,
    channel_id: int,
    name: str | None = None,
    is_private: bool | None = None,
    allowed_role_ids: list[int] | None = None,
) -> dict:
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if is_private is not None:
        body["is_private"] = is_private
    if allowed_role_ids is not None:
        body["allowed_role_ids"] = allowed_role_ids
    return client.request("PATCH", f"/api/channels/{channel_id}", json=body)


@_safe
def _channel_delete(client: LoungeClient, channel_id: int, confirm: bool = False) -> dict:
    if not confirm:
        return {"would_do": [f"DELETE channel {channel_id}"]}
    return client.request("DELETE", f"/api/channels/{channel_id}")


@_safe
def _category_create(client: LoungeClient, name: str) -> dict:
    wid = client.workspace_id()
    return client.request(
        "POST", f"/api/workspaces/{wid}/categories", json={"name": name}
    )


@_safe
def _thread_list(client: LoungeClient, channel_id: int) -> dict:
    return {"threads": client.request("GET", f"/api/channels/{channel_id}/threads")}


@_safe
def _thread_create(
    client: LoungeClient, channel_id: int, title: str, root_message_id: int | None = None
) -> dict:
    return client.request(
        "POST",
        f"/api/channels/{channel_id}/threads",
        json={"name": title, "root_message_id": root_message_id},
    )


@_safe
def _message_send(client: LoungeClient, channel_or_thread_id: int, content: str) -> dict:
    """Send to a channel; fall back to a thread if the id is a thread."""
    try:
        return client.request(
            "POST",
            f"/api/channels/{channel_or_thread_id}/messages",
            json={"content": content},
        )
    except LoungeError as exc:
        if exc.status == 404:
            return client.request(
                "POST",
                f"/api/threads/{channel_or_thread_id}/messages",
                json={"content": content},
            )
        raise


@_safe
def _message_edit(client: LoungeClient, message_id: int, content: str) -> dict:
    return client.request(
        "PATCH", f"/api/messages/{message_id}", json={"content": content}
    )


@_safe
def _message_delete(
    client: LoungeClient,
    channel_or_thread_id: int,
    message_id: int,
    confirm: bool = False,
) -> dict:
    if not confirm:
        return {"would_do": [f"DELETE message {message_id} in {channel_or_thread_id}"]}
    return client.request("DELETE", f"/api/messages/{message_id}")


@_safe
def _member_list(client: LoungeClient) -> dict:
    wid = client.workspace_id()
    return {"members": client.request("GET", f"/api/workspaces/{wid}/members")}


@_safe
def _bots_list(client: LoungeClient) -> dict:
    wid = client.workspace_id()
    return {"bots": client.request("GET", f"/api/workspaces/{wid}/bots")}


@_safe
def _bot_create(client: LoungeClient, name: str) -> dict:
    wid = client.workspace_id()
    return client.request("POST", f"/api/workspaces/{wid}/bots", json={"name": name})


@_safe
def _bot_provision(client: LoungeClient, name: str, watch_url: str | None = None) -> dict:
    wid = client.workspace_id()
    return client.request(
        "POST",
        f"/api/workspaces/{wid}/bots/provision",
        json={"name": name, "watch_url": watch_url},
    )


@_safe
def _bot_remove(client: LoungeClient, user_id: int, confirm: bool = False) -> dict:
    if not confirm:
        return {"would_do": [f"REMOVE bot user {user_id}"]}
    wid = client.workspace_id()
    return client.request("DELETE", f"/api/workspaces/{wid}/bots/{user_id}")


@_safe
def _role_list(client: LoungeClient) -> dict:
    wid = client.workspace_id()
    return {"roles": client.request("GET", f"/api/workspaces/{wid}/roles")}


@_safe
def _role_create(
    client: LoungeClient,
    name: str,
    can_manage_channels: bool = False,
    can_moderate: bool = False,
) -> dict:
    wid = client.workspace_id()
    return client.request(
        "POST",
        f"/api/workspaces/{wid}/roles",
        json={
            "name": name,
            "can_manage_channels": can_manage_channels,
            "can_moderate": can_moderate,
        },
    )


@_safe
def _role_assign(client: LoungeClient, user_id: int, role_id: int) -> dict:
    wid = client.workspace_id()
    return client.request(
        "POST", f"/api/workspaces/{wid}/members/{user_id}/roles/{role_id}"
    )


@_safe
def _role_remove(client: LoungeClient, user_id: int, role_id: int) -> dict:
    wid = client.workspace_id()
    return client.request(
        "DELETE", f"/api/workspaces/{wid}/members/{user_id}/roles/{role_id}"
    )


@_safe
def _role_delete(client: LoungeClient, role_id: int, confirm: bool = False) -> dict:
    if not confirm:
        return {"would_do": [f"DELETE role {role_id}"]}
    return client.request("DELETE", f"/api/roles/{role_id}")


@_safe
def _bot_set_roles(client: LoungeClient, user_id: int, role_ids: list[int]) -> dict:
    wid = client.workspace_id()
    return client.request(
        "PUT",
        f"/api/workspaces/{wid}/members/{user_id}/roles",
        json={"role_ids": role_ids},
    )


# --------------------------------------------------------------------------- #
# FastMCP registration
# --------------------------------------------------------------------------- #
_default_client: Optional[LoungeClient] = None


def get_client() -> LoungeClient:
    global _default_client
    if _default_client is None:
        _default_client = LoungeClient()
    return _default_client


def build_server():
    if _ServerClass is None:
        raise RuntimeError("mcp[cli] is required to run the MCP server")
    mcp = _ServerClass("lounge-ops")

    @mcp.tool()
    def workspace_overview() -> dict:
        """Counts plus the channel/category listing for the workspace."""
        return _workspace_overview(get_client())

    @mcp.tool()
    def channel_list() -> dict:
        """List channels and categories."""
        return _channel_list(get_client())

    @mcp.tool()
    def channel_info(channel_id: int) -> dict:
        """Inspect one channel."""
        return _channel_info(get_client(), channel_id)

    @mcp.tool()
    def channel_create(name: str, category_id: int | None = None) -> dict:
        """Create a text channel."""
        return _channel_create(get_client(), name, category_id)

    @mcp.tool()
    def channel_update(
        channel_id: int,
        name: str | None = None,
        is_private: bool | None = None,
        allowed_role_ids: list[int] | None = None,
    ) -> dict:
        """Update a channel's name/privacy/allowed roles."""
        return _channel_update(get_client(), channel_id, name, is_private, allowed_role_ids)

    @mcp.tool()
    def channel_delete(channel_id: int, confirm: bool = False) -> dict:
        """Delete a channel (confirm-gated)."""
        return _channel_delete(get_client(), channel_id, confirm)

    @mcp.tool()
    def category_create(name: str) -> dict:
        """Create a category."""
        return _category_create(get_client(), name)

    @mcp.tool()
    def thread_list(channel_id: int) -> dict:
        """List threads in a channel."""
        return _thread_list(get_client(), channel_id)

    @mcp.tool()
    def thread_create(channel_id: int, title: str, root_message_id: int | None = None) -> dict:
        """Create a thread in a channel."""
        return _thread_create(get_client(), channel_id, title, root_message_id)

    @mcp.tool()
    def message_send(channel_or_thread_id: int, content: str) -> dict:
        """Send a message to a channel or thread."""
        return _message_send(get_client(), channel_or_thread_id, content)

    @mcp.tool()
    def message_edit(message_id: int, content: str) -> dict:
        """Edit a message."""
        return _message_edit(get_client(), message_id, content)

    @mcp.tool()
    def message_delete(channel_or_thread_id: int, message_id: int, confirm: bool = False) -> dict:
        """Delete a message (confirm-gated)."""
        return _message_delete(get_client(), channel_or_thread_id, message_id, confirm)

    @mcp.tool()
    def member_list() -> dict:
        """List workspace members."""
        return _member_list(get_client())

    @mcp.tool()
    def bots_list() -> dict:
        """List bot members."""
        return _bots_list(get_client())

    @mcp.tool()
    def bot_create(name: str) -> dict:
        """Create a bot (token returned once)."""
        return _bot_create(get_client(), name)

    @mcp.tool()
    def bot_provision(name: str, watch_url: str | None = None) -> dict:
        """One-call bot workspace: role+category+private channel+assignment."""
        return _bot_provision(get_client(), name, watch_url)

    @mcp.tool()
    def bot_remove(user_id: int, confirm: bool = False) -> dict:
        """Remove a bot (confirm-gated)."""
        return _bot_remove(get_client(), user_id, confirm)

    @mcp.tool()
    def role_list() -> dict:
        """List roles."""
        return _role_list(get_client())

    @mcp.tool()
    def role_create(name: str, can_manage_channels: bool = False, can_moderate: bool = False) -> dict:
        """Create a role."""
        return _role_create(get_client(), name, can_manage_channels, can_moderate)

    @mcp.tool()
    def role_assign(user_id: int, role_id: int) -> dict:
        """Assign a role to a member."""
        return _role_assign(get_client(), user_id, role_id)

    @mcp.tool()
    def role_remove(user_id: int, role_id: int) -> dict:
        """Remove a role from a member."""
        return _role_remove(get_client(), user_id, role_id)

    @mcp.tool()
    def role_delete(role_id: int, confirm: bool = False) -> dict:
        """Delete a role (confirm-gated)."""
        return _role_delete(get_client(), role_id, confirm)

    @mcp.tool()
    def bot_set_roles(user_id: int, role_ids: list[int]) -> dict:
        """Replace a member's roles."""
        return _bot_set_roles(get_client(), user_id, role_ids)

    return mcp


def main() -> None:  # pragma: no cover - entry point
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
