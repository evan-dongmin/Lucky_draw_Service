import asyncio
import json
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import characters, departments as departments_module
from app import director, fairness, predictions, race, roster
from app.config import (
    CHARACTER_SNAPSHOT_PATH,
    PREDICTION_SNAPSHOT_PATH,
    STATIC_DIR,
)
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
    load_character_snapshot()
    mc_agent.load_cache()
    # 예전에는 운영자가 admin 콘솔에서 "MC 멘트 사전생성" 버튼을 따로 눌러야
    # 했다 -- 잊고 안 누르면 정적 폴백만 쓰이는데도 티가 안 나 운영 실수로
    # 이어지기 쉬웠다. 세션·참가자와 무관한 익명 템플릿 생성이라 서버 기동
    # 시점에 자동으로 한 번 돌려도 무방해서, 백그라운드 스레드로 실행해
    # 서버 시작을 막지 않게 한다(API 키가 없으면 has_llm=False라 즉시 반환).
    asyncio.create_task(asyncio.to_thread(mc_agent.pregenerate))
    yield


app = FastAPI(title="타추위 추첨 프로그램", lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """정적 파일(HTML/JS/CSS)에 캐시 방지 헤더를 강제한다.

    StaticFiles/FileResponse는 Cache-Control을 안 붙이므로 브라우저가
    자체 휴리스틱으로 오래 캐싱할 수 있다 -- 배포 후에도 운영 콘솔이
    새로고침 없이는 예전 admin.js를 계속 쓰다가(HTML은 새 버전인데 JS만
    옛 버전이라 DOM 참조가 어긋나는 등) 버튼이 조용히 먹통이 되는 사고로
    이어진다. ETag/Last-Modified 기반 재검증은 그대로 유지돼 매번 새로
    다운로드하지는 않는다.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path in ("/admin", "/mobile"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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
predict_tokens: dict[str, str] = {}  # 기기 토큰 -> participant_id (예측 게임/캐릭터 선택 공용 온보딩)
fast_forward_requests: set[str] = set()  # 조기 종료가 요청된 session_id 집합 (레이스 구간 전용)
character_choices: dict[str, str] = {}  # participant_id -> character_id (선택 안 하면 부서 기반 폴백)
prediction_snapshot_path = PREDICTION_SNAPSHOT_PATH
character_snapshot_path = CHARACTER_SNAPSHOT_PATH

# 최신 race_tick 스냅샷(라운드/진행률/통과선/위치/부서 실시간 통과율) 1개만
# 들고 있는다. race_tick 자체는 위치 데이터가 커서 Stage 전용으로만
# 브로드캐스트하지만(_run_race_phase 참고), 모바일은 "내 카트 등수 하나"만
# 필요하므로 굳이 250명에게 매 틱 뿌리는 대신 이 캐시에서 폴링 시점에
# 딱 한 명분만 계산해 돌려준다(app/main.py의 _my_race_status 참고).
latest_race_tick: dict[str, Any] | None = None


def save_prediction_snapshot() -> None:
    """예측 게임 상태를 디스크에 저장한다. Session과 마찬가지로
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
# 레이스 구간 시작 후 이 시간 동안은 카트를 출발선에 세워 둔다(스타트
# 라이트 시퀀스가 끝나고 나서 실제로 출발하도록).
#
# **static/stage.js의 LIGHT_* 상수(LIGHT_INTRO_MS/LIGHT_STEP_MS/
# LIGHT_HOLD_MAX_MS)와 반드시 함께 움직여야 한다.**
# 실제 F1처럼 "빨간 등 5개가 하나씩 켜지고 -> 불규칙한 정적 -> 일제 소등"
# 으로 연출을 늘리면서(사용자 요청) 2.8초 -> 5.2초로 키웠다. 이보다 짧으면
# 라이트가 켜져 있는 동안 카트가 이미 달려나가는 것이 보인다(과거 버그).
# duration_seconds * 0.25 상한이 함께 걸려 있어 짧은 구간에서는 자동으로
# 줄어든다.
RACE_COUNTDOWN_SECONDS = 5.2


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
        data["prize_winners"] = None
        data["prize_basis"] = None
        data["prize_scores"] = []
        data["round_pass_ids"] = {
            str(r): ids for r, ids in draw.round_pass_ids.items() if r in draw.revealed_rounds
        }
        # round_candidate_count[1]은 참가자 수·당첨 인원수 같은 공개 정보만으로
        # 계산되는 값이라 리빌 전에 보여도 안전하지만, [2]는 R1이 결승선
        # 컷오프(§12-8)로 몇 명이나 걸러졌는지를 드러내(레이스 진행 상황
        # 유출) round_pass_ids와 같은 규칙으로 가린다.
        data["round_candidate_count"] = {
            str(r): count
            for r, count in draw.round_candidate_count.items()
            if r == 1 or r in draw.revealed_rounds
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
    total_seconds: float = 600.0
    predictions_enabled: bool = False


def _cancel_active_races() -> None:
    """진행 중인 레이스 런북을 중단시킨다.

    **새 세션·재추첨·초기화 때 반드시 불러야 한다.** 안 부르면 옛 런북이
    계속 돌면서 마지막에 `fairness.reveal`까지 실행해 **옛 추첨 결과를 최종
    당첨자로 발표해버린다**. 재추첨은 같은 session_id에 draw만 덧붙이므로
    런북의 구간 경계 검사(`session.session_id != session_id`)에도 걸리지
    않아 끝까지 완주한다 -- 진행자는 다시 뽑았다고 생각하는데 화면에는
    재추첨 전 당첨자가 뜬다.

    취소를 await하지는 않는다. 이 함수는 state_lock을 쥔 채로 불리는데
    런북도 final_announce 구간에서 같은 락을 잡으므로, 기다리면 교착이
    생긴다. cancel()만 걸어두면 런북은 다음 await(틱 sleep)에서 곧바로
    빠져나온다.
    """
    global latest_race_tick
    for session_id, task in list(active_race_tasks.items()):
        if not task.done():
            task.cancel()
        active_race_tasks.pop(session_id, None)
    fast_forward_requests.clear()
    # 멈춘 레이스의 마지막 틱이 남아 있으면 폰의 "내 카트 현황"이 계속
    # 그 순간을 진행 중인 것처럼 보여준다.
    latest_race_tick = None


def _reset_prediction_state() -> None:
    """새 세션·재추첨·초기화 시 예측 엔진과 캐릭터 선택, 스냅샷 파일을 모두
    정리한다. 하나라도 남으면 다음 세션에 낡은 점수/신원이 새어 들어간다."""
    global latest_race_tick
    prediction_engine.reset()
    predict_tokens.clear()
    character_choices.clear()
    latest_race_tick = None
    if prediction_snapshot_path.exists():
        prediction_snapshot_path.unlink()
    if character_snapshot_path.exists():
        character_snapshot_path.unlink()


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
        # 진행 중이던 레이스가 있으면 먼저 끊는다 -- 세션이 바뀌어도 옛
        # 런북은 레이스 구간(기본 95초) 하나를 다 쓸 때까지 구간 경계 검사에
        # 도달하지 않아, 그동안 새 세션 화면에 옛 레이스 틱이 계속 흘러간다.
        _cancel_active_races()
        _reset_prediction_state()
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
        _cancel_active_races()
        _reset_prediction_state()
        await hub.broadcast({"type": "reset"})
        return {"ok": True}


# ---------------------------------------------------------------------------
# 추첨: 커밋 -> (연출) -> 리빌 -> 재추첨
# ---------------------------------------------------------------------------


def _post_countdown_race_seconds(duration_seconds: float) -> float:
    """스타트 라이트 리드인을 뺀 실제 레이스 진행 시간. `_run_race_phase`가
    실제 틱을 돌릴 때 쓰는 것과 반드시 같은 공식이어야 한다 -- 결승선
    컷오프(§12-8) 판정이 이 값을 기준으로 초 단위 창을 계산하므로,
    어긋나면 화면에서 보이는 컷오프 시점과 판정이 안 맞게 된다."""
    countdown = min(RACE_COUNTDOWN_SECONDS, duration_seconds * 0.25)
    return max(1e-6, duration_seconds - countdown)


def _race_cutoff_seconds_for_commit(session: Session) -> tuple[float | None, float | None]:
    """결승선 컷오프(§12-8) 계산에 쓸 race_r1/r2의 실제 레이스 시간을
    커밋 시점에 런북에서 미리 구한다. 레이싱 모드가 아니거나(룰렛)
    런북 계산이 실패하면(짧은 total_seconds 등) 컷오프 없이(순위만으로
    통과) 예전과 동일하게 동작하도록 (None, None)을 돌려준다."""
    if session.mode != "racing":
        return None, None
    try:
        segments = director.build_runbook(
            total_seconds=session.total_seconds, predictions_enabled=session.predictions_enabled
        )
    except director.DirectorError:
        return None, None
    by_phase = {seg.phase: seg.duration_seconds for seg in segments}
    r1 = _post_countdown_race_seconds(by_phase["race_r1"]) if "race_r1" in by_phase else None
    r2 = _post_countdown_race_seconds(by_phase["race_r2"]) if "race_r2" in by_phase else None
    return r1, r2


@app.post("/api/draw/commit")
async def commit_draw() -> dict[str, Any]:
    async with state_lock:
        session = _require_session()
        race_r1_seconds, race_r2_seconds = _race_cutoff_seconds_for_commit(session)
        try:
            draw = fairness.compute_draw(
                session_id=session.session_id,
                participants=session.participants,
                draw_count=session.draw_count,
                excluded_ids=session.excluded_ids,
                race_r1_seconds=race_r1_seconds,
                race_r2_seconds=race_r2_seconds,
            )
        except fairness.FairnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.draws.append(draw)
        store.set_session(session)
        if session.predictions_enabled:
            department_names = list(draw.snapshot.get("departments", {}).keys())
            await _open_round_1(session, draw, department_names)
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
        # 룰렛 모드는 레이싱 라운드/예측·갬블링 창이 전혀 안 돌기 때문에
        # 리더보드 기준 당첨자를 계산할 방법이 없다 -- 레이스 결과를 그대로
        # 실제 당첨자로 쓴다(아래 _compute_prize_winners의 predictions_enabled
        # =False 케이스와 동일한 폴백).
        draw.prize_winners = list(draw.winners)
        draw.prize_basis = "race"
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
        race_r1_seconds, race_r2_seconds = _race_cutoff_seconds_for_commit(session)
        try:
            draw = fairness.compute_draw(
                session_id=session.session_id,
                participants=session.participants,
                draw_count=session.draw_count,
                excluded_ids=session.excluded_ids,
                race_r1_seconds=race_r1_seconds,
                race_r2_seconds=race_r2_seconds,
            )
        except fairness.FairnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.draws.append(draw)
        store.set_session(session)
        # 레이스가 돌고 있는 중에 재추첨을 눌렀다면 옛 런북을 반드시 끊는다.
        # 재추첨은 session_id를 그대로 두고 draw만 덧붙이므로 런북의 구간
        # 경계 검사에 안 걸려 끝까지 완주하고, 결국 **재추첨 전 당첨자**를
        # 최종 발표해버린다(회귀 테스트로 고정).
        _cancel_active_races()
        if session.predictions_enabled:
            # 새 추첨 회차 -- 예측 게임도 처음부터 새로 시작한다
            prediction_engine.reset()
            department_names = list(draw.snapshot.get("departments", {}).keys())
            await _open_round_1(session, draw, department_names)
        public = public_draw_dict(draw)
        await hub.broadcast({"type": "commit_ready", "draw": public, "draw_index": len(session.draws) - 1})
        return public


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
    total = len(population)
    # 통과선(결승선) 위치는 순위 기준 후보군 크기(round_candidate_count)로
    # 계산한다 -- round_pass_ids의 실제 길이는 결승선 컷오프(§12-8)로 줄어들
    # 수 있는데, 그걸 기준으로 하면 시간에 밀려 탈락한(하지만 순위상으로는
    # 후보였던) 카트가 화면에서 아예 결승선에 못 미치는 것처럼 보여
    # "1등 통과 후 카운트다운"이라는 연출의 전제 자체가 깨진다. R3는
    # round_candidate_count가 없다(컷오프 대상이 아니라 순위 그대로).
    pass_count = draw.round_candidate_count.get(round_index, len(draw.round_pass_ids[round_index]))
    line = race.pass_line(pass_count, total)
    departments = draw.snapshot.get("departments", {})
    denom_sets = _department_denom_sets(departments, round_index, draw)

    # 스타트 라이트(3·2·1 GO) 리드인. 예전에는 라이트가 켜지는 동안 이미
    # 레이스가 진행돼 버려서, 카운트다운이 끝났을 땐 카트가 한참 나가
    # 있었다(사용자 피드백). 이 구간에는 ratio를 0으로 고정해 카트를
    # 출발선에 세워 둔다 -- 구간 전체 길이(duration_seconds)는 그대로라
    # 런북 시간 배분에는 영향이 없다. 아주 짧은 구간(테스트용 0.02초 등)
    # 에서는 리드인이 구간을 잡아먹지 않도록 비율로 제한한다.
    countdown = min(RACE_COUNTDOWN_SECONDS, duration_seconds * 0.25)
    race_seconds = _post_countdown_race_seconds(duration_seconds)
    # 결승선 컷오프(§12-8) 창 길이. R1/R2에만 있고, 화면이 "1등 결승 통과 후
    # 몇 초"인지 계산하려면 이 값이 필요하다 -- fairness.py의 상수를
    # 그대로 참조해 값이 어긋날 일이 없게 한다.
    cutoff_window_seconds = {
        1: fairness.R1_CUTOFF_WINDOW_SECONDS,
        2: fairness.R2_CUTOFF_WINDOW_SECONDS,
        3: fairness.R3_CUTOFF_WINDOW_SECONDS,
    }.get(round_index)

    # 결선(R3)의 카운트다운은 R1/R2와 성격이 다르다(2026-08-08, 사용자 요청:
    # "3라운드는 결승선 처음 통과한 카트 기준으로 카운트다운을 5초로 해 줘.
    # 카운트다운 끝나면 결과 발표하고").
    #
    # - R1/R2에서는 창이 "누가 통과하는가"를 가르지만, 레이스 자체는 구간
    #   시간을 다 쓴다.
    # - R3에서는 창이 닫히는 순간 **레이스를 끝낸다**. 남은 구간 시간을
    #   기다리지 않고 곧바로 결과 발표로 넘어가므로 피날레가 늘어지지 않는다.
    #
    # **당첨자 자체는 여전히 순위로 정해진다**(fairness.py가 커밋 시점에 이미
    # 확정). 결선 결승선은 애초에 "정확히 N대가 넘도록" 놓이기 때문에, 창을
    # 좁힌다고 해서 당첨자가 바뀔 수는 없다 -- 경품 수는 항상 채워져야 하고,
    # 늦게 들어온 카트를 떨어뜨려도 그 자리를 채울 사람은 더 뒤에 있는
    # 카트뿐이기 때문이다. 즉 R3의 창은 "언제 끝낼지"를 정하는 연출 규칙이다.
    r3_finish_deadline: float | None = None

    # **이 라운드에 의미 있는 결승선이 있는가.** 전원 통과(pass_line=-0.01)면
    # 출발하자마자 모든 카트가 "통과"로 잡히므로 컷오프·카운트다운·화면
    # 워프가 전부 망가진다(참가자 100명 이하면 R1이 항상 이 상태다).
    # 그런 라운드에는 컷오프 정보를 아예 안 내려보내 화면이 패널을 숨기게
    # 한다 -- 어차피 아무도 탈락하지 않는 순수 순위 라운드다.
    round_has_finish_line = race.has_finish_line(line)

    # 이 라운드의 장애물 배치는 (시드, 라운드, 결승선)으로만 정해지는 정적인
    # 값이라 매 틱 다시 계산할 필요 없이 한 번만 만든다(§12-4). 시드 자체는
    # 절대 전송하지 않고, 이미 파생된 배치(위치·레인·종류·움직임)만 내려준다.
    # 결승선을 함께 넘겨야 장애물이 **결승선 앞쪽 구간에만** 고르게 깔린다.
    obstacles = race.obstacle_layout(draw.seed, round_index, line)

    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        elapsed = loop.time() - start
        ratio = min(max((elapsed - countdown) / race_seconds, 0.0), 1.0)
        if session_id in fast_forward_requests:
            # 비상 조기 종료: 다음 틱에서 곧바로 최종 위치(ratio=1.0)로 점프한다.
            # 통과 판정은 position_at()이 ratio=1.0에서 목표값과 정확히 일치하도록
            # 설계되어 있으므로(test_race.py의 100회 반복 검증), 결과 정합성은
            # 그대로 유지된다 -- 시간만 절약될 뿐이다.
            fast_forward_requests.discard(session_id)
            ratio = 1.0
        positions = race.compute_tick(
            population, ratio, round_index, seed=draw.seed, pass_line_value=line
        )
        payload: dict[str, Any] = {
            "type": "race_tick",
            "round": round_index,
            "progress_ratio": ratio,
            "pass_line": line,
            "positions": positions,
            # 이 라운드의 장애물 배치(정적) + 지금 이 틱에 효과가 걸려 있는
            # 카트만 담은 맵. 위치(positions)에 이미 장애물 감속이 실제로
            # 반영돼 있으므로, 클라이언트는 이 값으로 스핀/사운드 연출만
            # 트리거하면 되고 충돌 판정을 직접 재현할 필요가 없다(§12-4).
            "obstacles": obstacles,
            "effects": race.compute_effects(
                population, ratio, round_index, draw.seed, pass_line_value=line
            ),
            # 스타트 라이트가 아직 켜져 있는(=출발 전) 틱인지. 무대 화면이
            # 이 구간에는 속도선·엔진음을 올리지 않고 그리드 정지 상태로
            # 보여주는 데 쓴다.
            "countdown": elapsed < countdown,
            # 결승선 컷오프(§12-8): 순위 기준 후보군 크기와 창 길이(초).
            # 클라이언트가 "1등 결승 통과" 순간을 스스로 감지해
            # (positions[pid] >= pass_line) 카운트다운을 띄우고, 그 사이
            # 결승선을 넘는 카트 수를 "N/후보수"로 실시간 표시한다.
            # R3도 같은 UI를 쓰되, 창이 닫히면 레이스가 실제로 끝난다.
            "candidate_count": pass_count if round_has_finish_line else None,
            "cutoff_window_seconds": cutoff_window_seconds if round_has_finish_line else None,
            # 화면이 진행률을 결승선 기준으로 워프할지 판단하는 값.
            # 결승선이 없으면 워프 없이 트랙 전체에 고르게 펼쳐야 한다.
            "has_finish_line": round_has_finish_line,
            # 결선에서 창이 닫혀 레이스를 조기 종료하는 틱인지. 무대가 이
            # 신호로 체커기 연출을 띄우고 렌더 루프를 정리한다.
            "race_over": False,
        }

        # 결선: 1등이 결승선을 넘는 순간부터 창(5초)을 재고, 창이 닫히면
        # 남은 구간 시간을 기다리지 않고 곧바로 결과 발표로 넘어간다.
        if round_index == 3 and cutoff_window_seconds and round_has_finish_line:
            if r3_finish_deadline is None and any(p >= line for p in positions.values()):
                r3_finish_deadline = elapsed + cutoff_window_seconds
            if r3_finish_deadline is not None and elapsed >= r3_finish_deadline:
                payload["race_over"] = True
        if denom_sets is not None:
            payload["department_live_rate"] = race.department_live_rates(positions, denom_sets, line)
        # race_tick은 위치 데이터 용량이 크므로 Stage 화면에만 전송한다
        # (모바일 250대에 프레임 데이터를 뿌리지 않는다는 원칙, 기획안 §4.7).
        # 대신 최신 틱 하나만 캐시해 두면, 모바일은 폴링 시점에 자기 한
        # 명분만 계산해서 받아갈 수 있다(_my_race_status).
        global latest_race_tick
        latest_race_tick = payload
        await hub.broadcast(payload, roles={"stage"})
        if ratio >= 1.0 or payload["race_over"]:
            break
        await asyncio.sleep(RACE_TICK_INTERVAL_SECONDS)


def _survivors_by_department(draw: DrawResult, round_index: int) -> dict[str, int]:
    """부서 그룹명 -> 그 라운드를 통과해 살아남은 카트 수(내림차순 정렬).

    라운드 사이 선택 창에서 "어느 팀이 몇 대 남았는지"를 무대에 띄우기
    위한 파생값이다. draw.round_pass_ids와 커밋된 스냅샷의 departments만
    있으면 계산되므로 새로운 추첨 로직은 전혀 들어가지 않는다.
    """
    passed = set(draw.round_pass_ids.get(round_index, []))
    counts = {
        name: sum(1 for pid in ids if pid in passed)
        for name, ids in draw.snapshot.get("departments", {}).items()
    }
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _finalists_in_order(draw: DrawResult) -> list[dict[str, Any]]:
    """결선(R3) 진출자를 등수 순으로. R2 종료 시점에만 의미가 있다.

    draw.ranking이 이미 확정 순위이므로 R2 통과자만 걸러내면 그대로
    결선 진출 등수가 된다(_ranked_targets와 같은 규칙 -- 무대에 보이는
    등수와 R3 예측 채점 순위가 어긋나지 않도록 의도적으로 맞췄다).
    """
    finalists = set(draw.round_pass_ids.get(2, []))
    dept_of = _department_by_pid(draw)
    return [
        {"participant_id": pid, "department": dept_of.get(pid, "")}
        for pid in draw.ranking
        if pid in finalists
    ]


async def _announce_round(session: Session, draw: DrawResult, round_index: int) -> None:
    async with state_lock:
        if round_index not in draw.revealed_rounds:
            draw.revealed_rounds.append(round_index)
            store.set_session(session)
    payload: dict[str, Any] = {
        "type": "round_revealed",
        "round": round_index,
        "pass_ids": draw.round_pass_ids[round_index],
        "department_pass_rate": draw.department_pass_rate.get(round_index, {}),
        # 무대의 라운드 전환기 패널용 파생 데이터(작업계획서 §12-2).
        # 새 메시지 타입을 만들지 않고 기존 메시지를 확장한 이유: 이 값들은
        # /api/session의 draw 스냅샷만으로 언제든 재구성 가능해서, 재접속
        # 복구 경로를 따로 만들 필요가 없다.
        "survivors_by_department": _survivors_by_department(draw, round_index),
    }
    if round_index == 2:
        payload["finalists"] = _finalists_in_order(draw)
    await hub.broadcast(payload)


async def _leaderboard_payload() -> dict[str, Any]:
    return {
        "type": "prediction_leaderboard",
        "top": [
            {"participant_id": c.participant_id, "score": c.score}
            for c in prediction_engine.leaderboard(10)
        ],
    }


def _department_by_pid(draw: DrawResult) -> dict[str, str]:
    """participant_id -> 예측 대상으로 쓰이는 부서 그룹명.

    커밋된 스냅샷의 departments(병합 후 5~8개 그룹)를 뒤집은 것이라,
    참가자의 원래 team 문자열이 아니라 **실제 예측 후보로 노출되는 이름**과
    항상 일치한다. 자동 배정 기본값이 후보에 없는 사태를 막는 핵심.
    """
    return {
        pid: name
        for name, ids in draw.snapshot.get("departments", {}).items()
        for pid in ids
    }


async def _open_round_1(session: Session, draw: DrawResult, department_names: list[str]) -> None:
    """커밋 직후(또는 재추첨 직후) 1라운드 선택 창을 연다.

    이때 명단 전원에게 카드를 만들어 둔다(사용자 요청) -- 모바일로 참여하지
    않은 사람도 R1·R2는 자기 부서가 자동 선택되므로 경품 가능성을 일단
    확보한다. 자세한 이유는 PredictionEngine.enroll_all 참고."""
    prediction_engine.enroll_all(_department_by_pid(draw))
    prediction_engine.open_round(1, department_names)
    save_prediction_snapshot()
    await hub.broadcast(
        {
            "type": "prediction_window",
            "round": 1,
            "state": "open",
            "candidates": department_names,
        }
    )


async def _lock_round(session: Session, round_index: int, seed: str) -> None:
    if prediction_engine.round_state.get(round_index) != "open":
        return
    prediction_engine.lock_round(round_index, seed=seed)
    save_prediction_snapshot()
    await hub.broadcast(
        {"type": "prediction_window", "round": round_index, "state": "locked"}
    )


def _ranked_targets(draw: DrawResult, scored_round: int) -> list[str]:
    """그 라운드 결과 순위대로 정렬된 예측 대상 목록(1위부터).

    R1·R2는 부서 통과율 내림차순, R3는 결선 진출자를 결승 등수 순으로
    나열한다. draw.ranking은 전체 참가자를 HMAC 점수 순으로 정렬해 둔
    확정 순위이므로, 여기서 결선 진출자만 걸러내면 곧 결승 등수가 된다.
    """
    if scored_round in (1, 2):
        return predictions.rank_targets_by_rate(draw.department_pass_rate.get(scored_round, {}))
    finalists = set(draw.round_pass_ids.get(2, []))
    return [pid for pid in draw.ranking if pid in finalists]


async def _score_and_open_next(
    session: Session, draw: DrawResult, scored_round: int, next_round: int | None
) -> None:
    ranked_targets = _ranked_targets(draw, scored_round)

    next_candidates: list[str] | None = None
    if next_round is not None:
        if next_round == 3:
            next_candidates = draw.round_pass_ids[2]
        else:
            next_candidates = list(draw.snapshot.get("departments", {}).keys())

    # 성과 점수 입력값: 이미 fairness.py가 계산해둔 결과(통과자·부서별
    # 통과율 순위·최종 당첨자)를 읽어서 넘길 뿐이라 추첨 계산에는 관여하지
    # 않는다. R3는 "통과"가 곧 최종 당첨이라 결선 당첨 점수만 얹는다.
    passed_ids = set(draw.round_pass_ids.get(scored_round, []))
    ranked_dept_ids: list[set[str]] = []
    final_winner_ids: set[str] = set()
    if scored_round in (1, 2):
        departments = draw.snapshot.get("departments", {})
        ranked_dept_ids = [
            set(departments.get(name, []))
            for name in predictions.rank_targets_by_rate(
                draw.department_pass_rate.get(scored_round, {})
            )
        ]
    else:
        passed_ids = set()
        final_winner_ids = set(draw.winners)

    prediction_engine.score_round(
        scored_round,
        ranked_targets,
        passed_ids=passed_ids,
        ranked_dept_ids=ranked_dept_ids,
        final_winner_ids=final_winner_ids,
        # 참가자가 고른 카트 능력이 예측 점수 규칙을 살짝 비튼다(§12-3).
        # 채점 시점의 선택을 그대로 쓰므로, 라운드 사이에 카트를 바꾸면
        # 그다음 라운드부터 새 능력이 적용된다(이미 채점된 라운드는 불변).
        character_by_pid=dict(character_choices),
    )
    if next_candidates is not None:
        prediction_engine.open_round(next_round, next_candidates)
    save_prediction_snapshot()
    await hub.broadcast({"type": "prediction_result", "round": scored_round})
    await hub.broadcast(await _leaderboard_payload())

    if next_round is not None:
        await hub.broadcast(
            {
                "type": "prediction_window",
                "round": next_round,
                "state": "open",
                "candidates": next_candidates,
            }
        )


def _compute_prize_winners(session: Session, draw: DrawResult) -> tuple[list[str], str, list[int]]:
    """실제 경품 당첨자를 정한다. 예측 게임이 켜져 있으면 그 최종 리더보드
    상위 N명(N = 원래 레이스로 정해졌던 당첨 인원수, len(draw.winners))이 곧
    당첨자다 -- 레이스 자체는 여전히(장애물까지 포함해) 시드만으로 100%
    결정론적으로 계산되지만, "그 레이스를 누가 가장 잘 예측했는가"가 최종
    결과를 정하는 구조로
    바꾼 것(사용자 요청: 막판까지 순위를 알 수 없고 리더보드가 끝까지
    갱신되는 반전 있는 진행을 위함). 예측 게임이 꺼져 있으면 리더보드 자체가
    없으므로 레이스 결과를 그대로 쓴다(기존 동작과 동일, 회귀 없음).

    leaderboard()는 이미 (-score, participant_id) 순으로 결정론적으로 정렬돼
    있어 동점 처리를 따로 할 필요가 없다. 참여 인원이 N명보다 적으면(모바일
    온보딩을 안 한 사람이 많은 경우) 그만큼 당첨자 수가 줄어든다 -- 예측
    게임에 참여해야 당첨 대상이 된다는 뜻이라 문서에 명확히 안내해야 한다.
    """
    n = len(draw.winners)
    if not session.predictions_enabled:
        # 레이스 결과 그대로 -- "성적" 개념이 없으므로 점수는 비운다.
        return list(draw.winners), "race", []
    ranked = prediction_engine.leaderboard(n)
    return [c.participant_id for c in ranked], "prediction", [c.score for c in ranked]


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
                    # 라운드 3 최종 채점(예측/갬블링 리더보드가 여기서 확정됨)이
                    # 끝난 뒤에야 실제 당첨자를 뽑는다 -- "revealed" 브로드캐스트
                    # 시점엔 아직 순위가 안 정해져 있다(막판까지 리더보드가
                    # 계속 뒤집힐 수 있다는 게 이 설계의 핵심 재미 포인트).
                    await _score_and_open_next(session, draw, 3, None)
                prize_ids, prize_basis, prize_scores = _compute_prize_winners(session, draw)
                async with state_lock:
                    draw.prize_winners = prize_ids
                    draw.prize_basis = prize_basis
                    draw.prize_scores = prize_scores
                    store.set_session(session)
                await hub.broadcast(
                    {
                        "type": "prize_winners",
                        "winners": prize_ids,
                        "basis": prize_basis,
                        "scores": prize_scores,
                    }
                )
                await asyncio.sleep(seg.duration_seconds)
            else:
                await asyncio.sleep(seg.duration_seconds)

        # 진행이 끝났으면 마지막 레이스 틱 캐시를 비운다. 안 비우면 모바일
        # "내 카트 현황"이 시상식 내내(그리고 행사가 끝난 뒤로도 영영)
        # 마지막 틱을 그대로 보여준다 -- 결선이 1위 통과 + 5초로 조기
        # 종료되면서부터는 진행률 1.0에 도달하기 전 스냅샷이 얼어붙어
        # "⏳ 진행 중 / 통과선까지 87% 진행"처럼 아직 달리는 것처럼 보인다.
        # None이 되면 _my_race_status/_my_department_rank가 None을 돌려주고,
        # 폰은 그 자리에 포인트 순위만 남긴다(정상 종료 상태).
        global latest_race_tick
        latest_race_tick = None
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


@app.post("/api/predict/join")
async def predict_join(payload: PredictJoinRequest) -> dict[str, Any]:
    """참가자 신원 확인(부서->실명 선택 후 발급되는 기기 토큰). 예측 게임과
    캐릭터 선택이 공유하는 단일 온보딩 단계다 -- 예측 게임이 꺼져 있는 순수
    레이싱 세션에서도 캐릭터를 고르려면 이 토큰이 필요하므로 레이싱
    모드이기만 하면 참여할 수 있다."""
    session = _require_session()
    _require_racing_mode(session)

    if payload.existing_token and payload.existing_token in predict_tokens:
        token = payload.existing_token
        pid = predict_tokens[token]
        card = prediction_engine.get_or_create_card(pid) if session.predictions_enabled else None
        return {
            "token": token,
            "participant_id": pid,
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
        card = prediction_engine.get_or_create_card(payload.participant_id)
        save_prediction_snapshot()
    return {
        "token": token,
        "participant_id": payload.participant_id,
        "predictions_enabled": session.predictions_enabled,
        "card": card.to_dict() if card else None,
    }


def _live_stats_full(round_index: int) -> dict[str, Any]:
    """무대·폰의 실시간 선택 통계용. 아직 아무도 안 고른 후보도 0명으로
    함께 내려 "진짜 소수파"가 목록에서 사라지지 않게 한다."""
    return prediction_engine.live_stats(
        round_index, candidates=prediction_engine.round_candidates.get(round_index, [])
    )


def _my_race_status(pid: str) -> dict[str, Any] | None:
    """모바일 "내 카트 현황" 카드용. latest_race_tick 캐시에서 이 참가자
    한 명분의 등수·통과 여부·통과선까지 진행률만 뽑아 계산한다(모바일에
    위치 데이터 자체를 뿌리지 않는다는 원칙은 그대로 유지 -- 사용자
    요청으로 "내 등수/통과 여부"만 폴링으로 노출).

    이번 라운드 생존자가 아니면(이전 라운드 탈락) positions에 없으므로
    None을 돌려준다 -- 프런트가 "이번 라운드는 참가 대상이 아님"으로
    구분해서 보여준다."""
    if latest_race_tick is None:
        return None
    positions: dict[str, float] = latest_race_tick["positions"]
    if pid not in positions:
        return None
    pos = positions[pid]
    pass_line = latest_race_tick["pass_line"]
    ranked = sorted(positions.items(), key=lambda kv: -kv[1])
    rank = next((i + 1 for i, (candidate, _) in enumerate(ranked) if candidate == pid), None)
    passed = pos >= pass_line
    progress_to_pass_pct = 100.0 if passed or pass_line <= 0 else min(100.0, max(0.0, pos / pass_line * 100))
    return {
        "round": latest_race_tick["round"],
        "rank": rank,
        "total": len(positions),
        "passed": passed,
        "progress_to_pass_pct": round(progress_to_pass_pct, 1),
    }


def _my_department_rank(pid: str) -> dict[str, Any] | None:
    """모바일 "우리 팀 순위"용. latest_race_tick에 실시간 부서 통과율이
    실려 있을 때만(R1·R2 -- R3는 부서 표시가 없다) 계산한다."""
    if latest_race_tick is None:
        return None
    rates: dict[str, float] | None = latest_race_tick.get("department_live_rate")
    if not rates:
        return None
    dept = prediction_engine.department_by_pid.get(pid)
    if dept is None or dept not in rates:
        return None
    ranked = predictions.rank_targets_by_rate(rates)
    rank = ranked.index(dept) + 1
    return {"department": dept, "rank": rank, "total": len(rates), "rate": rates[dept]}


def _candidate_stats(session: Session, round_index: int) -> dict[str, dict[str, Any]]:
    """예측 후보별 "고를 때 참고할 지표"(사용자 요청).

    라운드마다 판단 근거가 다르다:
      - R1: 아직 아무도 탈락하지 않았으므로 **팀별 참가 카트 수**가 유일한
        단서다(인원이 많은 팀이 통과자 수도 많을 가능성이 높다).
      - R2: 직전 R1을 통과해 **살아남은 카트 수 + R1 통과율 순위**.
      - R3: 후보가 결선 진출자 개인이므로 **직전 라운드 등수 + 소속 팀**.

    전부 이미 확정된 draw 값에서 파생될 뿐이라 추첨 계산에는 관여하지
    않는다. 아직 근거가 없는 시점(커밋 전 등)에는 빈 dict를 돌려주고,
    프런트는 그때 지표 없이 후보만 보여준다.
    """
    if not session.draws:
        return {}
    draw = session.draws[-1]
    departments: dict[str, list[str]] = draw.snapshot.get("departments", {})
    if round_index == 1:
        return {name: {"karts": len(ids)} for name, ids in departments.items()}
    if round_index == 2:
        survivors = _survivors_by_department(draw, 1)
        rates = draw.department_pass_rate.get(1, {})
        ranked = predictions.rank_targets_by_rate(rates)
        return {
            name: {
                "karts": survivors.get(name, 0),
                "prev_rank": ranked.index(name) + 1 if name in ranked else None,
                "prev_rate": rates.get(name),
            }
            for name in departments
        }
    # R3 -- 후보가 결선 진출자 개인이다. _finalists_in_order와 같은 순서를
    # 써서 무대 화면의 등수표와 폰의 표시가 어긋나지 않게 한다.
    return {
        f["participant_id"]: {"prev_rank": i + 1, "department": f["department"]}
        for i, f in enumerate(_finalists_in_order(draw))
    }


def _my_prize_result(session: Session, pid: str) -> dict[str, Any] | None:
    """이 참가자의 최종 당첨 여부. 아직 발표 전이면 None.

    무대 화면에는 시상대가 뜨지만 **폰에는 아무것도 안 뜨던 것**이 문제였다.
    참가자 입장에서 "내가 됐나?"는 행사 전체에서 가장 궁금한 한 가지인데,
    그걸 큰 화면에서 이름을 찾아 확인해야 했다.

    WS 이벤트(prize_winners)만으로 처리하면 그 순간 화면이 꺼져 있었거나
    새로고침한 사람은 영영 못 보므로, 폴링으로도 항상 같은 값을 받을 수
    있게 여기서 함께 내려준다."""
    if not session.draws:
        return None
    draw = session.draws[-1]
    if not draw.prize_winners:
        return None
    winners = list(draw.prize_winners)
    rank = winners.index(pid) + 1 if pid in winners else None
    return {
        "announced": True,
        "is_winner": rank is not None,
        "winner_rank": rank,
        "winner_count": len(winners),
        "basis": draw.prize_basis,
    }


@app.get("/api/predict/me")
async def predict_me(token: str) -> dict[str, Any]:
    session = _require_session()
    pid = _resolve_pid_from_token(token)
    if not session.predictions_enabled:
        # 예측 게임이 꺼진 순수 레이싱 세션 -- 카드를 만들 필요가 없다
        # (만들어봐야 라운드가 영원히 열리지 않는 죽은 데이터가 된다).
        #
        # 다만 **예측과 무관한 정보는 이쪽에서도 내려줘야 한다**. 예전에는
        # 여기서 곧바로 돌아가는 바람에, 순수 레이싱 세션의 참가자 폰은
        # 레이스 내내 아무것도 못 보고 당첨 결과조차 알 수 없었다
        # (이 모드에서 basis="race"로 당첨자가 정해지는데도).
        return {
            "predictions_enabled": False,
            "card": None,
            "participant_id": pid,
            "race_status": _my_race_status(pid),
            "department_rank": _my_department_rank(pid),
            "prize": _my_prize_result(session, pid),
        }
    card = prediction_engine.get_or_create_card(pid)
    open_rounds = [r for r, s in prediction_engine.round_state.items() if s == "open"]
    return {
        "predictions_enabled": True,
        "card": card.to_dict(),
        "round_state": prediction_engine.round_state,
        "round_candidates": prediction_engine.round_candidates,
        "live": {str(r): _live_stats_full(r) for r in open_rounds},
        # 열려 있는 라운드의 후보별 판단 지표(팀별 카트 수 / 직전 등수).
        # 무대 화면에만 있던 정보를 폰에서도 볼 수 있게 한 것(사용자 요청) --
        # 폰만 든 사람과 큰 화면을 보는 사람 사이의 정보 격차를 없앤다.
        "candidate_stats": {str(r): _candidate_stats(session, r) for r in open_rounds},
        "race_status": _my_race_status(pid),
        "department_rank": _my_department_rank(pid),
        "point_rank": prediction_engine.rank_of(pid),
        "point_total": len(prediction_engine.cards),
        # 최종 당첨 결과(발표 전에는 None). 폰에서 "내가 됐나?"를 바로
        # 확인할 수 있어야 한다 -- 예전에는 무대 시상대에서 이름을 찾는
        # 방법밖에 없었다.
        "prize": _my_prize_result(session, pid),
    }


@app.get("/api/predict/live")
async def predict_live() -> dict[str, Any]:
    """공개(토큰 불필요) 실시간 선택 분포 조회 -- Stage 화면이 주기적으로
    폴링해 "표가 몰립니다" 연출에 쓴다."""
    session = _require_session()
    _require_predictions_enabled(session)
    open_rounds = [r for r, s in prediction_engine.round_state.items() if s == "open"]
    return {"rounds": {str(r): _live_stats_full(r) for r in open_rounds}}


class PredictChooseRequest(BaseModel):
    token: str
    round: int
    target: str


@app.post("/api/predict/choose")
async def predict_choose(payload: PredictChooseRequest) -> dict[str, Any]:
    _require_session()
    pid = _resolve_pid_from_token(payload.token)
    try:
        card = prediction_engine.set_target(pid, payload.round, payload.target)
    except predictions.PredictionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_prediction_snapshot()
    return card.to_dict()


def _bot_play_open_rounds(participant_id: str) -> None:
    """데모 봇 한 명이 현재 열려 있는 라운드(들)에 참여한다 -- 무작위
    대상 선택."""
    card = prediction_engine.get_or_create_card(participant_id)
    for round_index, state in prediction_engine.round_state.items():
        if state == "open" and not card.locked[round_index]:
            candidates = prediction_engine.round_candidates[round_index]
            if candidates:
                prediction_engine.set_target(participant_id, round_index, random.choice(candidates))


@app.post("/api/predict/bots/fill")
async def predict_bots_fill() -> dict[str, Any]:
    """데모 모드 전용: 아직 참여하지 않은 참가자를 자동 참여시켜 분포·
    리더보드 요동을 재현한다. 행사 당일에는 호출하지 않는다(admin.html에
    "데모 전용"으로 표기)."""
    session = _require_session()
    _require_predictions_enabled(session)

    joined_pids = set(predict_tokens.values())
    filled = 0
    for participant in session.participants:
        if participant.id in joined_pids:
            continue
        token = uuid.uuid4().hex
        predict_tokens[token] = participant.id
        _bot_play_open_rounds(participant.id)
        filled += 1
    save_prediction_snapshot()
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
# 캐릭터/카트 선택: 참가자가 자기 카트의 특수능력을 직접 고른다(순수 연출).
# 고르지 않은 참가자는 부서 기반 자동 배정으로 폴백한다(static/stage.js).
# 예측 게임과 무관하게, 레이싱 세션이기만 하면 선택할 수 있다.
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
    total_seconds: float = 600.0
    with_bots: bool = True
    reserved_for_human: int = 5


@app.post("/api/demo/start")
async def demo_start(payload: DemoStartRequest) -> dict[str, Any]:
    """원클릭 데모: 샘플 명단 생성 -> 레이싱+예측 세션 생성 -> 커밋 ->
    (선택) 봇으로 채우기 -> 레이스 자동 시작까지 한 번에 수행한다.
    심사자가 배포 주소에 접속해 버튼 하나로 전체 흐름을 체험하기 위함."""
    try:
        director.build_runbook(total_seconds=payload.total_seconds, predictions_enabled=True)
    except director.DirectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with state_lock:
        store.clear()
        _reset_prediction_state()

        participants = roster.generate_sample_participants(count=payload.participant_count)
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            participants=participants,
            draw_count=payload.draw_count,
            mode="racing",
            total_seconds=payload.total_seconds,
            predictions_enabled=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.set_session(session)
        await hub.broadcast({"type": "session_created", "session": public_session_dict(session)})

        race_r1_seconds, race_r2_seconds = _race_cutoff_seconds_for_commit(session)
        try:
            draw = fairness.compute_draw(
                session_id=session.session_id,
                participants=session.participants,
                draw_count=session.draw_count,
                excluded_ids=session.excluded_ids,
                race_r1_seconds=race_r1_seconds,
                race_r2_seconds=race_r2_seconds,
            )
        except fairness.FairnessError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.draws.append(draw)
        store.set_session(session)

        department_names = list(draw.snapshot.get("departments", {}).keys())
        await _open_round_1(session, draw, department_names)
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
                _bot_play_open_rounds(participant.id)
                filled += 1
            save_prediction_snapshot()

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


@app.get("/mobile")
async def mobile_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "mobile.html")


# 응원 이모지(가벼운 화면 연출용). 임의 문자열이 그대로 무대 화면에
# 뿌려지는 걸 막기 위해 허용 목록만 통과시키고, 연결당 최소 간격을 둬서
# 연타 도배를 막는다(250명이 동시에 눌러도 서버 부하는 무시할 수준).
CHEER_EMOJI_ALLOWLIST = {"🔥", "👏", "🎉", "💪", "😱", "⚡", "❤️", "😂"}
CHEER_COOLDOWN_SECONDS = 0.4
_last_cheer_at: dict[WebSocket, float] = {}


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
            if message.get("type") == "cheer":
                emoji = message.get("emoji")
                now = time.monotonic()
                if emoji in CHEER_EMOJI_ALLOWLIST and now - _last_cheer_at.get(websocket, 0.0) >= CHEER_COOLDOWN_SECONDS:
                    _last_cheer_at[websocket] = now
                    await hub.broadcast({"type": "cheer", "emoji": emoji}, sender=websocket, roles={"stage"})
                continue  # 허용 밖/쿨다운 중이면 조용히 무시하고 범용 릴레이로 안 흘려보냄
            await hub.broadcast(message, sender=websocket)
    except WebSocketDisconnect:
        hub.disconnect(websocket)
        _last_cheer_at.pop(websocket, None)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
