import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        sockets = self.connections.get(user_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self.connections.pop(user_id, None)

    def user_ids(self) -> list[int]:
        return list(self.connections)

    def create_response(self, data: Any, status: str = "success") -> dict[str, Any]:
        return {"status": status, "data": data}

    async def send_json_msg(
        self, websocket: WebSocket, response: dict[str, Any]
    ) -> None:
        await websocket.send_json(response)

    async def send_to_user(self, user_id: int, response: dict[str, Any]) -> int:
        sent = 0
        for websocket in list(self.connections.get(user_id, ())):
            try:
                await websocket.send_json(response)
                sent += 1
            except RuntimeError:
                logger.info("Dropping stale websocket for user #%s", user_id)
                self.disconnect(user_id, websocket)
        return sent

    async def broadcast(self, response: dict[str, Any]) -> None:
        for user_id in self.user_ids():
            await self.send_to_user(user_id, response)


manager = ConnectionManager()
