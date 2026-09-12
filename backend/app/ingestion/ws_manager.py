"""Minimal in-process pub/sub fan-out for Phase 1.

Phase 2 replaces this with a real broker (MQTT, per REQUIREMENTS.md open question 5)
so multiple server processes/clients can scale independently. Keeping this behind a
small interface (`broadcast_position`) means swapping the transport later shouldn't
touch API/ingestion code that calls it.
"""

import json

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast_position(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        message = json.dumps(payload)
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
