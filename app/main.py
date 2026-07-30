import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import fairness, roster
from app.config import STATIC_DIR
from app.mc import MCAgent
from app.models import DrawResult, Participant, Session
from app.store import SessionStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lucky_draw")

store = SessionStore()
mc_agent = MCAgent()
state_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    session = store.load_snapshot()
    if session:
        logger.info("세션 스냅샷 복원: %s", session.session_id)
    mc_agent.load_cache()
    yield


app = FastAPI(title="타추위 추첨 프로그램", lifespan=lifespan)


class ConnectionHub:
    """WS 연결 관리: stage/admin/mobile 화면 간 상태 브로드캐스트."""

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


# ---------------------------------------------------------------------------
# 직렬화 헬퍼: 리빌 전에는 seed/ranking/winners/round_pass_ids를 숨긴다.
# 커밋 해시·부서 편성(snapshot 내 참가자/부서)은 처음부터 공개한다
# (온보딩·부서 소개는 추첨 전에 이뤄지므로).
# ---------------------------------------------------------------------------


def public_draw_dict(draw: DrawResult) -> dict[str, Any]:
    data = draw.to_dict()
    if not draw.revealed:
        data["seed"] = None
        data["winners"] = []
        data["ranking"] = []
        data["round_pass_ids"] = {}
        data["department_pass_rate"] = {}
    return data


def public_session_dict(session: Session) -> dict[str, Any]:
    data = session.to_dict()
    data["draws"] = [public_draw_dict(d) for d in session.draws]
    return data


def _require_session() -> Session:
    session = store.get_session()
    if session is None:
        raise HTTPException(status_code=404, detail="세션이 없습니다. 먼저 명단을 등록하세요.")
    return session


def _require_draw(session: Session, draw_index: int | None) -> DrawResult:
    if not session.draws:
        raise HTTPException(status_code=404, detail="추첨(커밋)이 아직 없습니다.")
    index = draw_index if draw_index is not None else len(session.draws) - 1
    if index < 0 or index >= len(session.draws):
        raise HTTPException(status_code=404, detail="존재하지 않는 추첨 번호입니다.")
    return session.draws[index]


# ---------------------------------------------------------------------------
# 명단 파싱 (미리보기 -- 세션 생성 전까지는 저장하지 않는다)
# ---------------------------------------------------------------------------


class RosterPreviewRequest(BaseModel):
    text: str


@app.post("/api/roster/preview")
async def roster_preview_text(payload: RosterPreviewRequest) -> dict[str, Any]:
    try:
        participants = roster.parse_roster_text(payload.text)
    except roster.RosterParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warnings = roster.validate_roster(participants)
    return {
        "participants": [p.to_dict() for p in participants],
        "warnings": warnings,
        "count": len(participants),
    }


@app.post("/api/roster/upload")
async def roster_upload_file(file: UploadFile) -> dict[str, Any]:
    data = await file.read()
    try:
        if file.filename and file.filename.lower().endswith(".xlsx"):
            participants = roster.parse_roster_xlsx(data)
        else:
            participants = roster.parse_roster_bytes(data)
    except roster.RosterParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    warnings = roster.validate_roster(participants)
    return {
        "participants": [p.to_dict() for p in participants],
        "warnings": warnings,
        "count": len(participants),
    }


@app.get("/api/roster/sample")
async def roster_sample(count: int = 250) -> dict[str, Any]:
    participants = roster.generate_sample_participants(count=count)
    return {
        "participants": [p.to_dict() for p in participants],
        "warnings": roster.validate_roster(participants),
        "count": len(participants),
    }


# ---------------------------------------------------------------------------
# 세션 관리
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    participants: list[dict[str, Any]]
    draw_count: int = 1
    mode: str = "roulette"


@app.post("/api/session")
async def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
    async with state_lock:
        participants = [Participant.from_dict(p) for p in payload.participants]
        if not participants:
            raise HTTPException(status_code=400, detail="참가자가 없습니다.")
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            participants=participants,
            draw_count=payload.draw_count,
            mode=payload.mode,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.set_session(session)
        await hub.broadcast({"type": "session_created", "session": public_session_dict(session)})
        return public_session_dict(session)


@app.get("/api/session")
async def get_session() -> dict[str, Any]:
    session = _require_session()
    return public_session_dict(session)


class ExcludeRequest(BaseModel):
    excluded_ids: list[str]


@app.post("/api/session/excluded")
async def set_excluded(payload: ExcludeRequest) -> dict[str, Any]:
    async with state_lock:
        session = _require_session()
        session.excluded_ids = list(dict.fromkeys(payload.excluded_ids))
        store.set_session(session)
        await hub.broadcast({"type": "session_updated", "session": public_session_dict(session)})
        return public_session_dict(session)


@app.post("/api/session/reset")
async def reset_session() -> dict[str, Any]:
    async with state_lock:
        store.clear()
        await hub.broadcast({"type": "reset"})
        return {"ok": True}


# ---------------------------------------------------------------------------
# 추첨: 커밋 -> (연출) -> 리빌 -> 재추첨
# ---------------------------------------------------------------------------


@app.post("/api/draw/commit")
async def commit_draw() -> dict[str, Any]:
    async with state_lock:
        session = _require_session()
        try:
            draw = fairness.compute_draw(
                session_id=session.session_id,
                participants=session.participants,
                draw_count=session.draw_count,
                excluded_ids=session.excluded_ids,
            )
        except fairness.FairnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.draws.append(draw)
        store.set_session(session)
        public = public_draw_dict(draw)
        await hub.broadcast({"type": "commit_ready", "draw": public, "draw_index": len(session.draws) - 1})
        return public


class RevealRequest(BaseModel):
    draw_index: int | None = None


@app.post("/api/draw/reveal")
async def reveal_draw(payload: RevealRequest) -> dict[str, Any]:
    async with state_lock:
        session = _require_session()
        draw = _require_draw(session, payload.draw_index)
        fairness.reveal(draw)
        store.set_session(session)
        public = public_draw_dict(draw)
        await hub.broadcast({"type": "revealed", "draw": public})
        return public


class RedrawRequest(BaseModel):
    exclude_previous_winners: bool = False


@app.post("/api/draw/redraw")
async def redraw(payload: RedrawRequest) -> dict[str, Any]:
    async with state_lock:
        session = _require_session()
        if payload.exclude_previous_winners and session.draws:
            last = session.draws[-1]
            if not last.revealed:
                raise HTTPException(status_code=400, detail="아직 리빌되지 않은 추첨입니다.")
            for winner_id in last.winners:
                if winner_id not in session.excluded_ids:
                    session.excluded_ids.append(winner_id)
        try:
            draw = fairness.compute_draw(
                session_id=session.session_id,
                participants=session.participants,
                draw_count=session.draw_count,
                excluded_ids=session.excluded_ids,
            )
        except fairness.FairnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.draws.append(draw)
        store.set_session(session)
        public = public_draw_dict(draw)
        await hub.broadcast({"type": "commit_ready", "draw": public, "draw_index": len(session.draws) - 1})
        return public


@app.get("/api/verify/{draw_index}")
async def verify_draw_endpoint(draw_index: int) -> dict[str, Any]:
    session = _require_session()
    draw = _require_draw(session, draw_index)
    if not draw.revealed:
        raise HTTPException(status_code=409, detail="아직 리빌되지 않았습니다.")
    recomputed = fairness.recompute_from_reveal(draw.seed, draw.snapshot)
    return {
        "seed": draw.seed,
        "snapshot": draw.snapshot,
        "declared_commit": draw.commit,
        "declared_winners": draw.winners,
        "declared_ranking": draw.ranking,
        "declared_round_pass_ids": {str(k): v for k, v in draw.round_pass_ids.items()},
        "server_recomputed_commit": recomputed["commit"],
        "server_recomputed_winners": recomputed["winners"],
        "matches": fairness.verify_draw(draw),
    }


# ---------------------------------------------------------------------------
# MC Agent: 사전 배치 생성 + 상황별 멘트 조회
# ---------------------------------------------------------------------------


@app.post("/api/mc/pregenerate")
async def mc_pregenerate() -> dict[str, Any]:
    async with state_lock:
        mc_agent.pregenerate()
        return {"has_llm": mc_agent.has_llm, "cached_tags": mc_agent.cached_tags}


@app.get("/api/mc/line/{tag}")
async def mc_line(tag: str) -> dict[str, Any]:
    session = store.get_session()
    params: dict[str, Any] = {}
    if session:
        params["participant_count"] = len(session.participants)
        params["department_count"] = len({p.team or "미지정" for p in session.participants})
        if session.draws:
            latest = session.draws[-1]
            if latest.revealed:
                params["winner_count"] = len(latest.winners)
    text = mc_agent.pick_line(tag, **params)
    return {"tag": tag, "text": text}


# ---------------------------------------------------------------------------
# 정적 페이지 + WS
# ---------------------------------------------------------------------------


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "stage.html")


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/verify")
async def verify_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "verify.html")


@app.get("/mobile")
async def mobile_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "mobile.html")


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
