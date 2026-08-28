"""
Менеджер WebSocket-соединений: рассылка персонализированного состояния игры
каждому подключённому клиенту (ведущему или команде) в комнате.
"""
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        # room_code -> list of dict{ws, role, team_id}
        self.rooms: dict[str, list[dict]] = {}

    async def connect(self, room_code: str, ws: WebSocket, role: str, team_id: str | None):
        await ws.accept()
        self.rooms.setdefault(room_code, []).append(
            {"ws": ws, "role": role, "team_id": team_id}
        )

    def disconnect(self, room_code: str, ws: WebSocket):
        conns = self.rooms.get(room_code, [])
        self.rooms[room_code] = [c for c in conns if c["ws"] is not ws]

    async def broadcast_state(self, room_code: str, build_view_fn):
        """
        build_view_fn(role, team_id) -> dict сериализованного состояния,
        персонализированного под конкретного получателя.
        """
        conns = self.rooms.get(room_code, [])
        dead = []
        for c in conns:
            try:
                payload = build_view_fn(c["role"], c["team_id"])
                await c["ws"].send_json(payload)
            except Exception:
                dead.append(c["ws"])
        for ws in dead:
            self.disconnect(room_code, ws)


manager = ConnectionManager()
