"""WebSocket connection hub + watch-hook delivery.

The hub tracks live connections per ``(workspace_id, user_id)`` so it can
fan out server→client events and report online presence.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

import httpx
from starlette.websockets import WebSocket


class Hub:
    def __init__(self) -> None:
        # workspace_id -> user_id -> set[WebSocket]
        self._conns: dict[int, dict[int, set[WebSocket]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self._lock = asyncio.Lock()

    async def register(self, workspace_id: int, user_id: int, ws: WebSocket) -> bool:
        """Register a connection. Returns True if the user just came online."""
        async with self._lock:
            bucket = self._conns[workspace_id][user_id]
            first = len(bucket) == 0
            bucket.add(ws)
            return first

    async def deregister(self, workspace_id: int, user_id: int, ws: WebSocket) -> bool:
        """Remove a connection. Returns True if the user went fully offline."""
        async with self._lock:
            bucket = self._conns.get(workspace_id, {}).get(user_id)
            if not bucket:
                return False
            bucket.discard(ws)
            if not bucket:
                del self._conns[workspace_id][user_id]
                return True
            return False

    def online_users(self, workspace_id: int) -> list[int]:
        return sorted(self._conns.get(workspace_id, {}).keys())

    async def broadcast(self, workspace_id: int, event: dict[str, Any]) -> None:
        targets: list[WebSocket] = []
        for bucket in self._conns.get(workspace_id, {}).values():
            targets.extend(bucket)
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:
                # Dead socket; cleanup happens on its own disconnect path.
                pass

    async def send_to(self, ws: WebSocket, event: dict[str, Any]) -> None:
        try:
            await ws.send_json(event)
        except Exception:
            pass


hub = Hub()


async def http_post_json(url: str, payload: dict[str, Any]) -> None:
    """POST a JSON payload to a watch-hook URL (best effort).

    Isolated here so tests can monkeypatch it instead of hitting the network.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(url, json=payload)


async def deliver_watch_hook(url: str, payload: dict[str, Any]) -> None:
    try:
        await http_post_json(url, payload)
    except Exception:
        # Watch-hook failures must never break message delivery.
        pass
