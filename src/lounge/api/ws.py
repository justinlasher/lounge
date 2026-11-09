"""WebSocket endpoint: presence, typing relay, and event fan-out."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import auth, events
from ..db import new_session

router = APIRouter()


@router.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket, wid: int, token: str = ""):
    session = new_session()
    try:
        user = auth.resolve_token(session, token)
        if user is None:
            await websocket.close(code=4401)
            return
        perms = auth.get_perms(session, wid, user)
        if not perms.is_member:
            await websocket.close(code=4403)
            return
        user_id = user.id
    finally:
        session.close()

    await websocket.accept()
    came_online = await events.hub.register(wid, user_id, websocket)

    # hello: current online user list
    await events.hub.send_to(
        websocket,
        {
            "type": "hello",
            "workspace_id": wid,
            "user_id": user_id,
            "online": events.hub.online_users(wid),
        },
    )
    if came_online:
        await events.hub.broadcast(
            wid, {"type": "presence.update", "user_id": user_id, "online": True}
        )

    try:
        while True:
            data = await websocket.receive_json()
            if isinstance(data, dict) and data.get("type") == "typing.start":
                channel_id = data.get("channel_id")
                await events.hub.broadcast(
                    wid,
                    {
                        "type": "typing.start",
                        "channel_id": channel_id,
                        "user_id": user_id,
                    },
                )
            # All other state changes go through REST; ignore anything else.
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        went_offline = await events.hub.deregister(wid, user_id, websocket)
        if went_offline:
            await events.hub.broadcast(
                wid,
                {"type": "presence.update", "user_id": user_id, "online": False},
            )
