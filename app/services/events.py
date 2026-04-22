from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket

from app.schemas.common import Event


class EventBus:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections[session_id].add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        self.connections[session_id].discard(websocket)
        if not self.connections[session_id]:
            self.connections.pop(session_id, None)

    async def publish(self, event: Event) -> None:
        peers = list(self.connections.get(event.session_id, set()))
        if not peers:
            return

        for websocket in peers:
            try:
                await websocket.send_json(event.model_dump(mode="json"))
            except Exception:
                self.disconnect(event.session_id, websocket)

    def publish_sync(self, event: Event) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            asyncio.run(self.publish(event))


event_bus = EventBus()
