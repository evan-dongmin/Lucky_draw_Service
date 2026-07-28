import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lucky_draw")

app = FastAPI(title="타추위 추첨 프로그램")


class ConnectionHub:
    """WS 연결 관리: stage/admin 화면 간 상태 브로드캐스트."""

    def __init__(self) -> None:
        self.connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.connections:
            self.connections.remove(websocket)

    async def broadcast(self, message: dict, sender: WebSocket | None = None) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        for connection in list(self.connections):
            if connection is sender:
                continue
            try:
                await connection.send_text(payload)
            except Exception:
                self.disconnect(connection)


hub = ConnectionHub()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "stage.html")


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/verify")
async def verify_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "verify.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                message = {"type": "echo", "raw": raw}
            await hub.broadcast(message, sender=websocket)
    except WebSocketDisconnect:
        hub.disconnect(websocket)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
