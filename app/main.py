import asyncio
import json
import logging
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import characters, departments as departments_module
from app import director, fairness, gambling, predictions, race, roster
from app.config import (
    CHARACTER_SNAPSHOT_PATH,
    GAMBLING_SNAPSHOT_PATH,
    PREDICTION_SNAPSHOT_PATH,
    STATIC_DIR,
)
from app.gambling import GamblingEngine
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
    load_prediction_snapshot()
    load_gambling_snapshot()
    load_character_snapshot()
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
gambling_engine = GamblingEngine()
predict_tokens: dict[str, str] = {}  # 기기 토큰 -> participant_id (예측/베팅 게임 공용 온보딩)
fast_forward_requests: set[str] = set()  # 조기 종료가 요청된 session_id 집합 (레이스 구간 전용)
character_choices: dict[str, str] = {}  # participant_id -> character_id (선택 안 하면 부서 기반 폴백)
prediction_snapshot_path = PREDICTION_SNAPSHOT_PATH
gambling_snapshot_path = GAMBLING_SNAPSHOT_PATH
character_snapshot_path = CHARACTER_SNAPSHOT_PATH


def save_prediction_snapshot() -> None:
    """예측 게임(확신도 배분) 상태를 디스크에 저장한다. Session과 마찬가지로
    서버가 재시작돼도 채점 결과가 사라지지 않도록(재계산이 아니라 그대로
    복원) 매 변경 시점마다 호출한다."""
    payload = {"engine": prediction_engine.to_dict(), "tokens": dict(predict_tokens)}
    prediction_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = prediction_snapshot_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(prediction_snapshot_path)


def load_prediction_snapshot() -> None:
    if not prediction_snapshot_path.exists():
        return
    data = json.loads(prediction_snapshot_path.read_text(encoding="utf-8"))
    prediction_engine.load_dict(data.get("engine", {}))
    predict_tokens.update(data.get("tokens", {}))


def save_gambling_snapshot() -> None:
    """갬블링 모드 상태(잔액·베팅·정산 이력)를 디스크에 저장한다. 확신도
    배분과 동일한 이유(장애 복구)로 매 변경 시점마다 호출한다. 세션당
    한 모드만 활성화되므로 실제로는 이 함수와 save_prediction_snapshot 중
    하나만 계속 호출되지만, tokens는 두 스냅샷 모두에 중복 저장해 둔다
    (재시작 시 어느 파일을 읽어도 참여자 신원 복원이 가능하도록)."""
    payload = {"engine": gambling_engine.to_dict(), "tokens": dict(predict_tokens)}
    gambling_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = gambling_snapshot_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(gambling_snapshot_path)


def load_gambling_snapshot() -> None:
    if not gambling_snapshot_path.exists():
        return
    data = json.loads(gambling_snapshot_path.read_text(encoding="utf-8"))
    gambling_engine.load_dict(data.get("engine", {}))
    predict_tokens.update(data.get("tokens", {}))


def save_character_snapshot() -> None:
    payload = {"choices": dict(character_choices)}
    character_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = character_snapshot_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(character_snapshot_path)


def load_character_snapshot() -> None:
    if not character_snapshot_path.exists():
        return
    data = json.loads(character_snapshot_path.read_text(encoding="utf-8"))
    character_choices.update(data.get("choices", {}))

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


VALID_PREDICTION_MODES = {"confidence", "gambling"}


class CreateSessionRequest(BaseModel):
    participants: list[dict[str, Any]]
    draw_count: int = 1
    mode: str = "roulette"
    total_seconds: float = 300.0
    predictions_enabled: bool = False
    prediction_mode: str = "confidence"


def _reset_prediction_and_gambling_state() -> None:
    """새 세션·재추첨·초기화 시 예측/베팅 두 엔진과 캐릭터 선택, 스냅샷
    파일을 모두 정리한다. 세션당 한 예측 모드만 쓰이지만, 이전 세션이
    다른 모드였을 수 있으므로 전부 확실히 비워야 다음 세션에서 낡은
    상태가 새지 않는다."""
    prediction_engine.reset()
    gambling_engine.reset()
    predict_tokens.clear()
    character_choices.clear()
    if prediction_snapshot_path.exists():
        prediction_snapshot_path.unlink()
    if gambling_snapshot_path.exists():
        gambling_snapshot_path.unlink()
    if character_snapshot_path.exists():
        character_snapshot_path.unlink()


@app.post("/api/session")
async def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
    async with state_lock:
        participants = [Participant.from_dict(p) for p in payload.participants]
        if not participants:
            raise HTTPException(status_code=400, detail="참가자가 없습니다.")
        if payload.prediction_mode not in VALID_PREDICTION_MODES:
            raise HTTPException(status_code=400, detail=f"prediction_mode는 {VALID_PREDICTION_MODES} 중 하나여야 합니다.")
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            participants=participants,
            draw_count=payload.draw_count,
            mode=payload.mode,
            total_seconds=payload.total_seconds,
            predictions_enabled=payload.predictions_enabled,
            prediction_mode=payload.prediction_mode,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.set_session(session)
        _reset_prediction_and_gambling_state()
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
        _reset_prediction_and_gambling_state()
        fast_forward_requests.clear()
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
            await _open_round_1(session, department_names)
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
            # 새 추첨 회차 -- 예측/베팅 게임도 새로 시작(활성 엔진만 리셋)
            if session.prediction_mode == "gambling":
                gambling_engine.reset()
            else:
                prediction_engine.reset()
            department_names = list(draw.snapshot.get("departments", {}).keys())
            await _open_round_1(session, department_names)
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


async def _run_race_phase(
    draw: DrawResult, round_index: int, duration_seconds: float, session_id: str
) -> None:
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
        if session_id in fast_forward_requests:
            # 비상 조기 종료: 다음 틱에서 곧바로 최종 위치(ratio=1.0)로 점프한다.
            # 통과 판정은 position_at()이 ratio=1.0에서 목표값과 정확히 일치하도록
            # 설계되어 있으므로(test_race.py의 100회 반복 검증), 결과 정합성은
            # 그대로 유지된다 -- 시간만 절약될 뿐이다.
            fast_forward_requests.discard(session_id)
            ratio = 1.0
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
        "mode": "confidence",
        "top": [
            {"participant_id": c.participant_id, "score": c.score}
            for c in prediction_engine.leaderboard(10)
        ],
    }


async def _gambling_leaderboard_payload() -> dict[str, Any]:
    # "prediction_leaderboard" 타입을 그대로 재사용한다 -- 클라이언트가 이미
    # 이 이벤트를 구독 중이므로 새 이벤트 타입을 추가하지 않고 mode 필드로
    # 분기시킨다(확신도 배분은 score, 갬블링은 balance가 랭킹 기준).
    return {
        "type": "prediction_leaderboard",
        "mode": "gambling",
        "top": [
            {"participant_id": c.participant_id, "balance": c.balance}
            for c in gambling_engine.leaderboard(10)
        ],
    }


async def _open_round_1(session: Session, department_names: list[str]) -> None:
    """커밋 직후(또는 재추첨 직후) 1라운드 선택/베팅 창을 연다. 활성 모드에
    따라 예측 엔진과 갬블링 엔진 중 하나만 움직인다."""
    if session.prediction_mode == "gambling":
        gambling_engine.open_round(1, department_names)
        save_gambling_snapshot()
    else:
        prediction_engine.open_round(1, department_names)
        save_prediction_snapshot()
    await hub.broadcast(
        {
            "type": "prediction_window",
            "round": 1,
            "state": "open",
            "candidates": department_names,
            "mode": session.prediction_mode,
        }
    )


async def _lock_round(session: Session, round_index: int, seed: str) -> None:
    if session.prediction_mode == "gambling":
        if gambling_engine.round_state.get(round_index) != "open":
            return
        gambling_engine.lock_round(round_index)
        save_gambling_snapshot()
    else:
        if prediction_engine.round_state.get(round_index) != "open":
            return
        prediction_engine.lock_round(round_index, seed=seed)
        save_prediction_snapshot()
    await hub.broadcast(
        {"type": "prediction_window", "round": round_index, "state": "locked", "mode": session.prediction_mode}
    )


async def _score_and_open_next(
    session: Session, draw: DrawResult, scored_round: int, next_round: int | None
) -> None:
    if scored_round in (1, 2):
        hit_set = predictions.top_k_by_rate(
            draw.department_pass_rate.get(scored_round, {}), 2 if scored_round == 1 else 1
        )
    else:
        hit_set = set(draw.winners)

    next_candidates: list[str] | None = None
    if next_round is not None:
        if next_round == 3:
            next_candidates = draw.round_pass_ids[2]
        else:
            next_candidates = list(draw.snapshot.get("departments", {}).keys())

    if session.prediction_mode == "gambling":
        odds_payload = gambling_engine.resolve_round(scored_round, hit_set)
        if next_candidates is not None:
            gambling_engine.open_round(next_round, next_candidates)
        save_gambling_snapshot()
        await hub.broadcast({"type": "gambling_result", **odds_payload})
        await hub.broadcast(await _gambling_leaderboard_payload())
    else:
        prediction_engine.score_round(scored_round, hit_set)
        if next_candidates is not None:
            prediction_engine.open_round(next_round, next_candidates)
        save_prediction_snapshot()
        await hub.broadcast(await _leaderboard_payload())

    if next_round is not None:
        await hub.broadcast(
            {
                "type": "prediction_window",
                "round": next_round,
                "state": "open",
                "candidates": next_candidates,
                "mode": session.prediction_mode,
            }
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
                await _lock_round(session, RACE_ROUND_INDEX[seg.phase], draw.seed)

            if seg.phase in RACE_ROUND_INDEX:
                await _run_race_phase(draw, RACE_ROUND_INDEX[seg.phase], seg.duration_seconds, session_id)
            elif seg.phase in SCORE_PHASE_ROUND:
                round_index = SCORE_PHASE_ROUND[seg.phase]
                await _announce_round(session, draw, round_index)
                if predictions_enabled:
                    await _score_and_open_next(session, draw, round_index, round_index + 1)
                await asyncio.sleep(seg.duration_seconds)
            elif seg.phase == "final_announce":
                async with state_lock:
                    fairness.reveal(draw)
                    store.set_session(session)
                await hub.broadcast({"type": "revealed", "draw": public_draw_dict(draw)})
                if predictions_enabled:
                    await _score_and_open_next(session, draw, 3, None)
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

    # run_racing_sequence는 백그라운드 태스크라서 director.DirectorError가 나도
    # 로그에만 남고 응답에는 드러나지 않는다 -- 총 시간이 선택창 하한(30초 x2)
    # 보다 짧으면 커밋 화면에서 영원히 멈춘 것처럼 보이는 사고로 이어진다.
    # 여기서 미리 같은 검증을 돌려 잘못된 총 시간을 즉시 400으로 되돌려준다.
    try:
        director.build_runbook(
            total_seconds=session.total_seconds, predictions_enabled=session.predictions_enabled
        )
    except director.DirectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    draw_index = payload.draw_index if payload.draw_index is not None else len(session.draws) - 1
    task = asyncio.create_task(
        run_racing_sequence(session.session_id, draw_index, session.total_seconds)
    )
    active_race_tasks[session.session_id] = task
    return {"started": True, "total_seconds": session.total_seconds, "draw_index": draw_index}


@app.post("/api/racing/fast-forward")
async def racing_fast_forward() -> dict[str, Any]:
    """비상용: 현재 진행 중인 레이스 구간(race_r1/r2/r3)만 조기 종료한다.
    선택창이 포함된 구간(score_rX_select_rY)은 30초 하한 원칙을 지키기 위해
    이 기능의 대상이 아니다."""
    session = _require_session()
    if session.mode != "racing":
        raise HTTPException(status_code=400, detail="레이싱 모드 세션이 아닙니다.")
    if session.session_id not in active_race_tasks or active_race_tasks[session.session_id].done():
        raise HTTPException(status_code=400, detail="진행 중인 레이스가 없습니다.")
    fast_forward_requests.add(session.session_id)
    return {"requested": True}


# ---------------------------------------------------------------------------
# 참여형 예측 게임: QR 온보딩(부서->실명) + 확신도 배분 + 대상 선택
# ---------------------------------------------------------------------------


def _require_predictions_enabled(session: Session) -> None:
    if not session.predictions_enabled:
        raise HTTPException(status_code=400, detail="이 세션은 예측 게임이 활성화되어 있지 않습니다.")


def _require_racing_mode(session: Session) -> None:
    if session.mode != "racing":
        raise HTTPException(status_code=400, detail="레이싱 모드 세션이 아닙니다.")


def _resolve_pid_from_token(token: str) -> str:
    pid = predict_tokens.get(token)
    if pid is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 참여 토큰입니다.")
    return pid


@app.get("/api/predict/departments")
async def predict_departments() -> dict[str, Any]:
    """부서->참가자 목록. 모바일 온보딩(부서->실명 선택)에 쓰이며, 캐릭터
    선택만 하는 경우에도 필요하므로 예측/베팅 게임이 꺼져 있어도(레이싱
    모드이기만 하면) 조회 가능하다."""
    session = _require_session()
    _require_racing_mode(session)
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


def _active_engine(session: Session):
    return gambling_engine if session.prediction_mode == "gambling" else prediction_engine


def _save_active_snapshot(session: Session) -> None:
    if session.prediction_mode == "gambling":
        save_gambling_snapshot()
    else:
        save_prediction_snapshot()


@app.post("/api/predict/join")
async def predict_join(payload: PredictJoinRequest) -> dict[str, Any]:
    """참가자 신원 확인(부서->실명 선택 후 발급되는 기기 토큰). 예측/베팅
    게임과 캐릭터 선택이 공유하는 단일 온보딩 단계다 -- 예측 게임이 꺼져
    있는 순수 레이싱 세션에서도 캐릭터를 고르려면 이 토큰이 필요하므로
    레이싱 모드이기만 하면 참여할 수 있다."""
    session = _require_session()
    _require_racing_mode(session)
    engine = _active_engine(session)

    if payload.existing_token and payload.existing_token in predict_tokens:
        token = payload.existing_token
        pid = predict_tokens[token]
        card = engine.get_or_create_card(pid) if session.predictions_enabled else None
        return {
            "token": token,
            "participant_id": pid,
            "mode": session.prediction_mode,
            "predictions_enabled": session.predictions_enabled,
            "card": card.to_dict() if card else None,
        }

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
    card = None
    if session.predictions_enabled:
        card = engine.get_or_create_card(payload.participant_id)
        _save_active_snapshot(session)
    return {
        "token": token,
        "participant_id": payload.participant_id,
        "mode": session.prediction_mode,
        "predictions_enabled": session.predictions_enabled,
        "card": card.to_dict() if card else None,
    }


def _live_stats(session: Session, round_index: int) -> dict[str, Any]:
    """선택/베팅 창이 열려 있는 동안의 실시간 통계. 확신도 배분은 선택
    분포(%), 갬블링은 패리뮤추얼 배당률 -- 둘 다 상태를 바꾸지 않는 순수
    조회라 자주 폴링해도 안전하다."""
    if session.prediction_mode == "gambling":
        return gambling_engine.live_odds(round_index)
    return {"round": round_index, "distribution": prediction_engine.live_distribution(round_index)}


@app.get("/api/predict/me")
async def predict_me(token: str) -> dict[str, Any]:
    session = _require_session()
    pid = _resolve_pid_from_token(token)
    if not session.predictions_enabled:
        # 예측/베팅 게임이 꺼진 순수 레이싱 세션 -- 카드를 만들 필요가
        # 없다(만들어봐야 라운드가 영원히 열리지 않는 죽은 데이터가 된다).
        return {"predictions_enabled": False, "mode": None, "card": None, "participant_id": pid}
    engine = _active_engine(session)
    card = engine.get_or_create_card(pid)
    open_rounds = [r for r, s in engine.round_state.items() if s == "open"]
    return {
        "predictions_enabled": True,
        "mode": session.prediction_mode,
        "card": card.to_dict(),
        "round_state": engine.round_state,
        "round_candidates": engine.round_candidates,
        "live": {str(r): _live_stats(session, r) for r in open_rounds},
    }


@app.get("/api/predict/live")
async def predict_live() -> dict[str, Any]:
    """공개(토큰 불필요) 실시간 분포/배당률 조회 -- Stage 화면이 주기적으로
    폴링해 "표가 몰립니다"/배당률 요동 연출에 쓴다."""
    session = _require_session()
    _require_predictions_enabled(session)
    engine = _active_engine(session)
    open_rounds = [r for r, s in engine.round_state.items() if s == "open"]
    return {
        "mode": session.prediction_mode,
        "rounds": {str(r): _live_stats(session, r) for r in open_rounds},
    }


class PredictAllocateRequest(BaseModel):
    token: str
    alloc: dict[int, int]


def _require_confidence_mode(session: Session) -> None:
    if session.prediction_mode != "confidence":
        raise HTTPException(status_code=400, detail="이 세션은 확신도 배분 모드가 아닙니다(갬블링 모드 -- /api/bet/* 사용).")


@app.post("/api/predict/allocate")
async def predict_allocate(payload: PredictAllocateRequest) -> dict[str, Any]:
    session = _require_session()
    _require_confidence_mode(session)
    pid = _resolve_pid_from_token(payload.token)
    try:
        card = prediction_engine.set_allocation(pid, payload.alloc)
    except predictions.PredictionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_prediction_snapshot()
    return card.to_dict()


class PredictChooseRequest(BaseModel):
    token: str
    round: int
    target: str


@app.post("/api/predict/choose")
async def predict_choose(payload: PredictChooseRequest) -> dict[str, Any]:
    session = _require_session()
    _require_confidence_mode(session)
    pid = _resolve_pid_from_token(payload.token)
    try:
        card = prediction_engine.set_target(pid, payload.round, payload.target)
    except predictions.PredictionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_prediction_snapshot()
    return card.to_dict()


def _random_valid_alloc() -> dict[int, int]:
    """확신도 100을 3라운드에 무작위로 배분(각 최소 10) -- 데모 봇 전용."""
    remaining = predictions.TOTAL_ALLOC - predictions.MIN_ALLOC * 3  # 자유롭게 분배할 나머지
    cut1 = random.randint(0, remaining)
    cut2 = random.randint(0, remaining)
    lo, hi = sorted([cut1, cut2])
    extra = [lo, hi - lo, remaining - hi]
    return {1: predictions.MIN_ALLOC + extra[0], 2: predictions.MIN_ALLOC + extra[1], 3: predictions.MIN_ALLOC + extra[2]}


def _bot_random_bet_amount(balance: int) -> int:
    """잔액의 10~40%를 무작위로 건다 -- 매번 전액 몰빵하면 데모에서
    부자연스럽고, 몇 라운드 안 가 다들 파산해 화면이 밋밋해진다."""
    if balance <= 0:
        return 0
    lo = max(1, int(balance * 0.1))
    hi = max(lo, int(balance * 0.4))
    return random.randint(lo, hi)


def _bot_play_open_rounds(participant_id: str, session: Session) -> None:
    """데모 봇 한 명이 현재 열려 있는 라운드(들)에 참여한다. 활성 모드에
    따라 확신도 배분(무작위 alloc+대상) 또는 갬블링(무작위 베팅)으로 분기한다."""
    if session.prediction_mode == "gambling":
        for round_index, state in gambling_engine.round_state.items():
            if state != "open":
                continue
            card = gambling_engine.get_or_create_card(participant_id)
            candidates = gambling_engine.round_candidates[round_index]
            if not candidates:
                continue
            amount = _bot_random_bet_amount(card.balance)
            if amount > 0:
                gambling_engine.place_bet(participant_id, round_index, random.choice(candidates), amount)
    else:
        card = prediction_engine.get_or_create_card(participant_id)
        card.alloc = _random_valid_alloc()
        for round_index, state in prediction_engine.round_state.items():
            if state == "open" and not card.locked[round_index]:
                candidates = prediction_engine.round_candidates[round_index]
                if candidates:
                    prediction_engine.set_target(participant_id, round_index, random.choice(candidates))


@app.post("/api/predict/bots/fill")
async def predict_bots_fill() -> dict[str, Any]:
    """데모 모드 전용: 아직 참여하지 않은 참가자를 자동 참여시켜 분포·
    리더보드 요동을 재현한다(확신도 배분/갬블링 모두 지원). 행사 당일에는
    호출하지 않는다(admin.html에 "데모 전용"으로 표기)."""
    session = _require_session()
    _require_predictions_enabled(session)

    joined_pids = set(predict_tokens.values())
    filled = 0
    for participant in session.participants:
        if participant.id in joined_pids:
            continue
        token = uuid.uuid4().hex
        predict_tokens[token] = participant.id
        _bot_play_open_rounds(participant.id, session)
        filled += 1
    _save_active_snapshot(session)
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
# 사이버머니 갬블링 (승인됨): 패리뮤추얼 베팅. prediction_mode="gambling"인
# 세션에서만 쓰인다 -- 확신도 배분과 동시에 참가자에게 강요하지 않는다.
# ---------------------------------------------------------------------------


def _require_gambling_mode(session: Session) -> None:
    if session.prediction_mode != "gambling":
        raise HTTPException(status_code=400, detail="이 세션은 갬블링 모드가 아닙니다.")


class BetPlaceRequest(BaseModel):
    token: str
    round: int
    target: str
    amount: int


@app.post("/api/bet/place")
async def bet_place(payload: BetPlaceRequest) -> dict[str, Any]:
    session = _require_session()
    _require_gambling_mode(session)
    pid = _resolve_pid_from_token(payload.token)
    try:
        card = gambling_engine.place_bet(pid, payload.round, payload.target, payload.amount)
    except gambling.GamblingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_gambling_snapshot()
    return card.to_dict()


@app.get("/api/bet/leaderboard")
async def bet_leaderboard(top_n: int = 10) -> dict[str, Any]:
    session = store.get_session()
    by_id = {p.id: p for p in session.participants} if session else {}
    top = gambling_engine.leaderboard(top_n)
    return {
        "top": [
            {
                "participant_id": c.participant_id,
                "name": by_id[c.participant_id].name if c.participant_id in by_id else c.participant_id,
                "balance": c.balance,
            }
            for c in top
        ]
    }


# ---------------------------------------------------------------------------
# 캐릭터/카트 선택: 참가자가 자기 카트의 특수능력을 직접 고른다(순수 연출).
# 고르지 않은 참가자는 부서 기반 자동 배정으로 폴백한다(static/stage.js).
# 예측/베팅 게임과 무관하게, 레이싱 세션이기만 하면 선택할 수 있다.
# ---------------------------------------------------------------------------


@app.get("/api/character/roster")
async def character_roster() -> dict[str, Any]:
    return {"roster": characters.CHARACTER_ROSTER}


class CharacterChooseRequest(BaseModel):
    token: str
    character_id: str


@app.post("/api/character/choose")
async def character_choose(payload: CharacterChooseRequest) -> dict[str, Any]:
    pid = _resolve_pid_from_token(payload.token)
    if payload.character_id not in characters.CHARACTER_IDS:
        raise HTTPException(status_code=400, detail="알 수 없는 캐릭터입니다.")
    character_choices[pid] = payload.character_id
    save_character_snapshot()
    return {"participant_id": pid, "character_id": payload.character_id}


@app.get("/api/character/me")
async def character_me(token: str) -> dict[str, Any]:
    pid = _resolve_pid_from_token(token)
    return {"participant_id": pid, "character_id": character_choices.get(pid)}


@app.get("/api/character/choices")
async def character_choices_endpoint() -> dict[str, Any]:
    """전원의 선택 현황(공개, 토큰 불필요) -- Stage 화면이 폴링해 추월
    이펙트·MC 해설에 참가자별 선택을 반영하는 데 쓴다."""
    return {"choices": dict(character_choices)}


# ---------------------------------------------------------------------------
# MC Agent: 사전 배치 생성 + 상황별 멘트 조회
# ---------------------------------------------------------------------------


@app.post("/api/mc/pregenerate")
async def mc_pregenerate() -> dict[str, Any]:
    async with state_lock:
        mc_agent.pregenerate()
        return {"has_llm": mc_agent.has_llm, "cached_tags": mc_agent.cached_tags}


@app.get("/api/mc/line/{tag}")
async def mc_line(
    tag: str,
    team: str | None = None,
    pass_count: int | None = None,
    rank: int | None = None,
    round: int | None = None,
    ability: str | None = None,
) -> dict[str, Any]:
    """상황별 멘트 조회. team/pass_count/rank/round/ability는 호출측(Stage)이
    실시간 이벤트(선두 교체·추월·라운드 통과 발표·팀 특수능력 발동)에서 이미
    들고 있는 값을 그대로 넘겨 자막에 채워 넣기 위한 선택적 오버라이드다."""
    session = store.get_session()
    params: dict[str, Any] = {}
    if session:
        params["participant_count"] = len(session.participants)
        params["department_count"] = len({p.team or "미지정" for p in session.participants})
        if session.draws:
            latest = session.draws[-1]
            if latest.revealed:
                params["winner_count"] = len(latest.winners)
    if team is not None:
        params["team"] = team
    if pass_count is not None:
        params["pass_count"] = pass_count
    if rank is not None:
        params["rank"] = rank
    if round is not None:
        params["round"] = round
    if ability is not None:
        params["ability"] = ability
    text = mc_agent.pick_line(tag, **params)
    return {"tag": tag, "text": text}


# ---------------------------------------------------------------------------
# 데모 모드 (심사자/원클릭 체험용) + 참여 QR 코드
# ---------------------------------------------------------------------------


@app.get("/api/qrcode")
async def qrcode_image(request: Request) -> Response:
    """모바일 참여 화면(/mobile) 접속용 QR 코드. Stage 화면 온보딩 구간에 표시된다."""
    import io

    import qrcode

    url = str(request.base_url) + "mobile"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


class DemoStartRequest(BaseModel):
    participant_count: int = 250
    draw_count: int = 3
    total_seconds: float = 300.0
    with_bots: bool = True
    reserved_for_human: int = 5
    prediction_mode: str = "confidence"


@app.post("/api/demo/start")
async def demo_start(payload: DemoStartRequest) -> dict[str, Any]:
    """원클릭 데모: 샘플 명단 생성 -> 레이싱+예측/갬블링 세션 생성 -> 커밋 ->
    (선택) 봇으로 채우기 -> 레이스 자동 시작까지 한 번에 수행한다.
    심사자가 배포 주소에 접속해 버튼 하나로 전체 흐름을 체험하기 위함."""
    if payload.prediction_mode not in VALID_PREDICTION_MODES:
        raise HTTPException(status_code=400, detail=f"prediction_mode는 {VALID_PREDICTION_MODES} 중 하나여야 합니다.")
    try:
        director.build_runbook(total_seconds=payload.total_seconds, predictions_enabled=True)
    except director.DirectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with state_lock:
        store.clear()
        _reset_prediction_and_gambling_state()

        participants = roster.generate_sample_participants(count=payload.participant_count)
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            participants=participants,
            draw_count=payload.draw_count,
            mode="racing",
            total_seconds=payload.total_seconds,
            predictions_enabled=True,
            prediction_mode=payload.prediction_mode,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.set_session(session)
        await hub.broadcast({"type": "session_created", "session": public_session_dict(session)})

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

        department_names = list(draw.snapshot.get("departments", {}).keys())
        await _open_round_1(session, department_names)
        public = public_draw_dict(draw)
        await hub.broadcast({"type": "commit_ready", "draw": public, "draw_index": 0})

        filled = 0
        if payload.with_bots:
            # 앞쪽 N명은 봇으로 채우지 않고 남겨둔다 -- 안 그러면 전원이
            # 봇에게 선점당해 실제 심사자/테스터가 /mobile에서 아무 이름도
            # 고를 수 없게 된다("이미 다른 기기에서 참여 중" 오류).
            reserved_ids = {p.id for p in session.participants[: max(payload.reserved_for_human, 0)]}
            joined_pids = set(predict_tokens.values())
            for participant in session.participants:
                if participant.id in joined_pids or participant.id in reserved_ids:
                    continue
                token = uuid.uuid4().hex
                predict_tokens[token] = participant.id
                _bot_play_open_rounds(participant.id, session)
                filled += 1
            _save_active_snapshot(session)

    task = asyncio.create_task(run_racing_sequence(session.session_id, 0, session.total_seconds))
    active_race_tasks[session.session_id] = task

    return {"session_id": session.session_id, "bots_filled": filled, "total_seconds": session.total_seconds}


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
