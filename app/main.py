import asyncio
import json
import logging
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import departments as departments_module
from app import director, fairness, predictions, race, roster
from app.config import STATIC_DIR
from app.mc import MCAgent
from app.models import DrawResult, Participant, Session
from app.predictions import PredictionEngine
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
    """WS 연결 관리: stage/admin/mobile 화면 간 상태 브로드캐스트.

    역할(role)별로 필터링해 보낼 수 있다 -- 특히 레이스 위치 틱("race_tick")은
    Stage 화면에만 필요하고 250대 모바일에 그대로 뿌리면 안 되므로(기획안의
    "모바일에 프레임 데이터 전송 금지" 원칙), roles 인자로 수신 대상을 제한한다.
    """

    def __init__(self) -> None:
        self.connections: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, role: str = "unknown") -> None:
        await websocket.accept()
        self.connections[websocket] = role

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.pop(websocket, None)

    async def broadcast(
        self, message: dict, sender: WebSocket | None = None, roles: set[str] | None = None
    ) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        for connection, role in list(self.connections.items()):
            if connection is sender:
                continue
            if roles is not None and role not in roles:
                continue
            try:
                await connection.send_text(payload)
            except Exception:
                self.disconnect(connection)


hub = ConnectionHub()
active_race_tasks: dict[str, asyncio.Task] = {}
prediction_engine = PredictionEngine()
predict_tokens: dict[str, str] = {}  # 기기 토큰 -> participant_id (예측 게임 온보딩용)

RACE_ROUND_INDEX = {"race_r1": 1, "race_r2": 2, "race_r3": 3}
SCORE_PHASE_ROUND = {"score_r1_select_r2": 1, "score_r2_select_r3": 2}
RACE_TICK_INTERVAL_SECONDS = 0.3


# ---------------------------------------------------------------------------
# 직렬화 헬퍼: 리빌 전에는 seed/ranking/winners/round_pass_ids를 숨긴다.
# 커밋 해시·부서 편성(snapshot 내 참가자/부서)은 처음부터 공개한다
# (온보딩·부서 소개는 추첨 전에 이뤄지므로).
# ---------------------------------------------------------------------------


def public_draw_dict(draw: DrawResult) -> dict[str, Any]:
    """리빌 전에도 레이싱 모드는 라운드가 끝날 때마다 그 라운드의 통과자만
    점진 공개한다(revealed_rounds). 최종 당첨자(winners)·전체 순위(ranking)·
    시드는 전체 리빌(draw.revealed) 전까지 절대 공개하지 않는다."""
    data = draw.to_dict()
    if not draw.revealed:
        data["seed"] = None
        data["winners"] = []
        data["ranking"] = []
        data["round_pass_ids"] = {
            str(r): ids for r, ids in draw.round_pass_ids.items() if r in draw.revealed_rounds
        }
        data["department_pass_rate"] = {
            str(r): rates
            for r, rates in draw.department_pass_rate.items()
            if r in draw.revealed_rounds
        }
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
    total_seconds: float = 300.0
    predictions_enabled: bool = False


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
            total_seconds=payload.total_seconds,
            predictions_enabled=payload.predictions_enabled,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.set_session(session)
        prediction_engine.reset()  # 새 세션 -- 예측 카드/토큰 초기화
        predict_tokens.clear()
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
        prediction_engine.reset()
        predict_tokens.clear()
        for session_id, task in list(active_race_tasks.items()):
            if not task.done():
                task.cancel()
            active_race_tasks.pop(session_id, None)
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
        if session.predictions_enabled:
            department_names = list(draw.snapshot.get("departments", {}).keys())
            prediction_engine.open_round(1, department_names)
            await hub.broadcast(
                {"type": "prediction_window", "round": 1, "state": "open", "candidates": department_names}
            )
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
        if session.predictions_enabled:
            prediction_engine.reset()  # 새 추첨 회차 -- 예측 게임도 새로 시작
            department_names = list(draw.snapshot.get("departments", {}).keys())
            prediction_engine.open_round(1, department_names)
            await hub.broadcast(
                {"type": "prediction_window", "round": 1, "state": "open", "candidates": department_names}
            )
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
# 레이싱 런북 자동 진행 (Director Agent) -- 3라운드 부서 대항 퍼널
# ---------------------------------------------------------------------------


def _department_denom_sets(
    departments: dict[str, list[str]], round_index: int, draw: DrawResult
) -> dict[str, set[str]] | None:
    """fairness.py의 _department_pass_rates와 동일한 분모 규칙.
    R1은 부서 전체, R2는 부서 ∩ R1 통과자. R3는 부서 표시를 하지 않는다."""
    if round_index == 1:
        return {name: set(ids) for name, ids in departments.items()}
    if round_index == 2:
        r1_set = set(draw.round_pass_ids[1])
        return {name: set(ids) & r1_set for name, ids in departments.items()}
    return None


async def _run_race_phase(draw: DrawResult, round_index: int, duration_seconds: float) -> None:
    population = draw.ranking if round_index == 1 else draw.round_pass_ids[round_index - 1]
    pass_count = len(draw.round_pass_ids[round_index])
    total = len(population)
    line = race.pass_line(pass_count, total)
    departments = draw.snapshot.get("departments", {})
    denom_sets = _department_denom_sets(departments, round_index, draw)

    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        elapsed = loop.time() - start
        ratio = min(elapsed / duration_seconds, 1.0) if duration_seconds > 0 else 1.0
        positions = race.compute_tick(population, ratio, round_index)
        payload: dict[str, Any] = {
            "type": "race_tick",
            "round": round_index,
            "progress_ratio": ratio,
            "pass_line": line,
            "positions": positions,
        }
        if denom_sets is not None:
            payload["department_live_rate"] = race.department_live_rates(positions, denom_sets, line)
        # race_tick은 위치 데이터 용량이 크므로 Stage 화면에만 전송한다
        # (모바일 250대에 프레임 데이터를 뿌리지 않는다는 원칙, 기획안 §4.7).
        await hub.broadcast(payload, roles={"stage"})
        if ratio >= 1.0:
            break
        await asyncio.sleep(RACE_TICK_INTERVAL_SECONDS)


async def _announce_round(session: Session, draw: DrawResult, round_index: int) -> None:
    async with state_lock:
        if round_index not in draw.revealed_rounds:
            draw.revealed_rounds.append(round_index)
            store.set_session(session)
    await hub.broadcast(
        {
            "type": "round_revealed",
            "round": round_index,
            "pass_ids": draw.round_pass_ids[round_index],
            "department_pass_rate": draw.department_pass_rate.get(round_index, {}),
        }
    )


async def _leaderboard_payload() -> dict[str, Any]:
    return {
        "type": "prediction_leaderboard",
        "top": [
            {"participant_id": c.participant_id, "score": c.score}
            for c in prediction_engine.leaderboard(10)
        ],
    }


async def _lock_prediction_round(round_index: int, seed: str) -> None:
    if prediction_engine.round_state.get(round_index) != "open":
        return
    prediction_engine.lock_round(round_index, seed=seed)
    await hub.broadcast({"type": "prediction_window", "round": round_index, "state": "locked"})


async def _score_and_open_next(draw: DrawResult, scored_round: int, next_round: int | None) -> None:
    if scored_round in (1, 2):
        hit_set = predictions.top_k_by_rate(
            draw.department_pass_rate.get(scored_round, {}), 2 if scored_round == 1 else 1
        )
    else:
        hit_set = set(draw.winners)
    prediction_engine.score_round(scored_round, hit_set)
    await hub.broadcast(await _leaderboard_payload())

    if next_round is not None:
        if next_round == 3:
            candidates = draw.round_pass_ids[2]
        else:
            candidates = list(draw.snapshot.get("departments", {}).keys())
        prediction_engine.open_round(next_round, candidates)
        await hub.broadcast(
            {"type": "prediction_window", "round": next_round, "state": "open", "candidates": candidates}
        )


async def run_racing_sequence(session_id: str, draw_index: int, total_seconds: float) -> None:
    try:
        session = store.get_session()
        if session is None or session.session_id != session_id:
            return
        predictions_enabled = session.predictions_enabled
        segments = director.build_runbook(total_seconds=total_seconds, predictions_enabled=predictions_enabled)
        for seg in segments:
            session = store.get_session()
            if session is None or session.session_id != session_id:
                return  # 세션이 초기화되었으면 조용히 중단
            if draw_index >= len(session.draws):
                return
            draw = session.draws[draw_index]

            await hub.broadcast(
                {
                    "type": "phase",
                    "phase": seg.phase,
                    "duration_seconds": seg.duration_seconds,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            )

            if predictions_enabled and seg.phase in RACE_ROUND_INDEX:
                await _lock_prediction_round(RACE_ROUND_INDEX[seg.phase], draw.seed)

            if seg.phase in RACE_ROUND_INDEX:
                await _run_race_phase(draw, RACE_ROUND_INDEX[seg.phase], seg.duration_seconds)
            elif seg.phase in SCORE_PHASE_ROUND:
                round_index = SCORE_PHASE_ROUND[seg.phase]
                await _announce_round(session, draw, round_index)
                if predictions_enabled:
                    await _score_and_open_next(draw, round_index, round_index + 1)
                await asyncio.sleep(seg.duration_seconds)
            elif seg.phase == "final_announce":
                async with state_lock:
                    fairness.reveal(draw)
                    store.set_session(session)
                await hub.broadcast({"type": "revealed", "draw": public_draw_dict(draw)})
                if predictions_enabled:
                    await _score_and_open_next(draw, 3, None)
                await asyncio.sleep(seg.duration_seconds)
            else:
                await asyncio.sleep(seg.duration_seconds)

        await hub.broadcast({"type": "racing_complete"})
    except Exception:
        logger.exception("레이싱 런북 진행 중 오류 -- 세션 %s", session_id)
    finally:
        active_race_tasks.pop(session_id, None)


class RacingStartRequest(BaseModel):
    draw_index: int | None = None


@app.post("/api/racing/start")
async def start_racing(payload: RacingStartRequest) -> dict[str, Any]:
    session = _require_session()
    if session.mode != "racing":
        raise HTTPException(status_code=400, detail="레이싱 모드 세션이 아닙니다.")
    draw = _require_draw(session, payload.draw_index)
    if draw.revealed:
        raise HTTPException(status_code=400, detail="이미 리빌된 추첨입니다. 재추첨 후 시작하세요.")
    if session.session_id in active_race_tasks and not active_race_tasks[session.session_id].done():
        raise HTTPException(status_code=409, detail="이미 레이스가 진행 중입니다.")

    draw_index = payload.draw_index if payload.draw_index is not None else len(session.draws) - 1
    task = asyncio.create_task(
        run_racing_sequence(session.session_id, draw_index, session.total_seconds)
    )
    active_race_tasks[session.session_id] = task
    return {"started": True, "total_seconds": session.total_seconds, "draw_index": draw_index}


# ---------------------------------------------------------------------------
# 참여형 예측 게임: QR 온보딩(부서->실명) + 확신도 배분 + 대상 선택
# ---------------------------------------------------------------------------


def _require_predictions_enabled(session: Session) -> None:
    if not session.predictions_enabled:
        raise HTTPException(status_code=400, detail="이 세션은 예측 게임이 활성화되어 있지 않습니다.")


def _resolve_pid_from_token(token: str) -> str:
    pid = predict_tokens.get(token)
    if pid is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 참여 토큰입니다.")
    return pid


@app.get("/api/predict/departments")
async def predict_departments() -> dict[str, Any]:
    session = _require_session()
    _require_predictions_enabled(session)
    groups = departments_module.compute_department_groups(session.participants)
    by_id = {p.id: p for p in session.participants}
    return {
        name: [
            {"id": pid, "name": by_id[pid].name}
            for pid in ids
            if pid in by_id
        ]
        for name, ids in groups.items()
    }


class PredictJoinRequest(BaseModel):
    participant_id: str
    existing_token: str | None = None


@app.post("/api/predict/join")
async def predict_join(payload: PredictJoinRequest) -> dict[str, Any]:
    session = _require_session()
    _require_predictions_enabled(session)

    if payload.existing_token and payload.existing_token in predict_tokens:
        token = payload.existing_token
        pid = predict_tokens[token]
        card = prediction_engine.get_or_create_card(pid)
        return {"token": token, "participant_id": pid, "card": card.to_dict()}

    participant = next((p for p in session.participants if p.id == payload.participant_id), None)
    if participant is None:
        raise HTTPException(status_code=404, detail="명단에서 참가자를 찾을 수 없습니다.")

    existing_token = next(
        (tok for tok, pid in predict_tokens.items() if pid == payload.participant_id), None
    )
    if existing_token:
        raise HTTPException(
            status_code=409, detail="이미 다른 기기에서 참여 중인 참가자입니다. 관리자에게 문의하세요."
        )

    token = uuid.uuid4().hex
    predict_tokens[token] = payload.participant_id
    card = prediction_engine.get_or_create_card(payload.participant_id)
    return {"token": token, "participant_id": payload.participant_id, "card": card.to_dict()}


@app.get("/api/predict/me")
async def predict_me(token: str) -> dict[str, Any]:
    pid = _resolve_pid_from_token(token)
    card = prediction_engine.get_or_create_card(pid)
    return {
        "card": card.to_dict(),
        "round_state": prediction_engine.round_state,
        "round_candidates": prediction_engine.round_candidates,
    }


class PredictAllocateRequest(BaseModel):
    token: str
    alloc: dict[int, int]


@app.post("/api/predict/allocate")
async def predict_allocate(payload: PredictAllocateRequest) -> dict[str, Any]:
    pid = _resolve_pid_from_token(payload.token)
    try:
        card = prediction_engine.set_allocation(pid, payload.alloc)
    except predictions.PredictionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return card.to_dict()


class PredictChooseRequest(BaseModel):
    token: str
    round: int
    target: str


@app.post("/api/predict/choose")
async def predict_choose(payload: PredictChooseRequest) -> dict[str, Any]:
    pid = _resolve_pid_from_token(payload.token)
    try:
        card = prediction_engine.set_target(pid, payload.round, payload.target)
    except predictions.PredictionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return card.to_dict()


def _random_valid_alloc() -> dict[int, int]:
    """확신도 100을 3라운드에 무작위로 배분(각 최소 10) -- 데모 봇 전용."""
    remaining = predictions.TOTAL_ALLOC - predictions.MIN_ALLOC * 3  # 자유롭게 분배할 나머지
    cut1 = random.randint(0, remaining)
    cut2 = random.randint(0, remaining)
    lo, hi = sorted([cut1, cut2])
    extra = [lo, hi - lo, remaining - hi]
    return {1: predictions.MIN_ALLOC + extra[0], 2: predictions.MIN_ALLOC + extra[1], 3: predictions.MIN_ALLOC + extra[2]}


@app.post("/api/predict/bots/fill")
async def predict_bots_fill() -> dict[str, Any]:
    """데모 모드 전용: 아직 참여하지 않은 참가자를 무작위 확신도·대상으로
    자동 참여시켜 예측 분포·리더보드 요동을 재현한다. 행사 당일에는 호출하지
    않는다(admin.html에 "데모 전용"으로 표기)."""
    session = _require_session()
    _require_predictions_enabled(session)

    joined_pids = set(predict_tokens.values())
    filled = 0
    for participant in session.participants:
        if participant.id in joined_pids:
            continue
        token = uuid.uuid4().hex
        predict_tokens[token] = participant.id
        card = prediction_engine.get_or_create_card(participant.id)
        card.alloc = _random_valid_alloc()
        for round_index, state in prediction_engine.round_state.items():
            if state == "open" and not card.locked[round_index]:
                candidates = prediction_engine.round_candidates[round_index]
                if candidates:
                    prediction_engine.set_target(participant.id, round_index, random.choice(candidates))
        filled += 1
    return {"filled": filled}


@app.get("/api/predict/leaderboard")
async def predict_leaderboard(top_n: int = 10) -> dict[str, Any]:
    session = store.get_session()
    by_id = {p.id: p for p in session.participants} if session else {}
    top = prediction_engine.leaderboard(top_n)
    return {
        "top": [
            {
                "participant_id": c.participant_id,
                "name": by_id[c.participant_id].name if c.participant_id in by_id else c.participant_id,
                "score": c.score,
            }
            for c in top
        ]
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
async def websocket_endpoint(websocket: WebSocket, role: str = "unknown") -> None:
    await hub.connect(websocket, role=role)
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
