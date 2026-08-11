import asyncio

import pytest

from app import fairness
from app import main as main_module
from app.director import RunbookSegment
from app.models import Session
from app.roster import generate_sample_participants

TINY_SEGMENTS = [
    RunbookSegment(phase="opening", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r1", duration_seconds=0.05, is_selection_window=False),
    RunbookSegment(phase="score_r1_select_r2", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r2", duration_seconds=0.05, is_selection_window=False),
    RunbookSegment(phase="score_r2_select_r3", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r3", duration_seconds=0.05, is_selection_window=False),
    RunbookSegment(phase="final_announce", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="verify", duration_seconds=0.01, is_selection_window=False),
]


@pytest.mark.asyncio
async def test_run_racing_sequence_full_flow(monkeypatch):
    participants = generate_sample_participants(30, seed=1)
    draw = fairness.compute_draw("race-test", participants, draw_count=3, seed="race-flow-seed")
    session = Session(
        session_id="race-test",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("race-test", 0, 300.0)

    types_seen = [m["type"] for m in messages]
    assert types_seen.count("phase") == len(TINY_SEGMENTS)
    assert "racing_complete" in types_seen

    round_revealed = [m for m in messages if m["type"] == "round_revealed"]
    assert [m["round"] for m in round_revealed] == [1, 2]
    assert round_revealed[0]["pass_ids"] == draw.round_pass_ids[1]
    assert round_revealed[1]["pass_ids"] == draw.round_pass_ids[2]

    revealed_msgs = [m for m in messages if m["type"] == "revealed"]
    assert len(revealed_msgs) == 1
    assert revealed_msgs[0]["draw"]["winners"] == draw.winners

    assert draw.revealed is True
    assert draw.revealed_rounds == [1, 2]

    tick_msgs = [m for m in messages if m["type"] == "race_tick"]
    assert tick_msgs
    r1_ticks = [m for m in tick_msgs if m["round"] == 1]
    assert r1_ticks[-1]["progress_ratio"] == pytest.approx(1.0)
    assert "department_live_rate" in r1_ticks[-1]

    r3_ticks = [m for m in tick_msgs if m["round"] == 3]
    assert r3_ticks
    assert "department_live_rate" not in r3_ticks[-1]  # R3는 부서 표시를 하지 않음


@pytest.mark.asyncio
async def test_racing_sequence_stops_gracefully_if_session_reset_midway(monkeypatch):
    participants = generate_sample_participants(20, seed=2)
    draw = fairness.compute_draw("race-test-2", participants, draw_count=2, seed="reset-mid-seed")
    session = Session(
        session_id="race-test-2",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)
        if message.get("type") == "phase" and message.get("phase") == "race_r1":
            main_module.store.clear()  # 진행 도중 세션이 초기화된 상황을 흉내낸다

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("race-test-2", 0, 300.0)

    # 예외 없이 조용히 중단되어야 하고, "racing_complete"까지 가지 않아야 한다
    assert "racing_complete" not in [m["type"] for m in messages]


@pytest.mark.asyncio
async def test_round_revealed_carries_transition_panel_data(monkeypatch):
    """무대의 라운드 전환기 패널(작업계획서 §12-2)에 필요한 파생 데이터가
    round_revealed에 실려 나가는지 확인한다.

    새 메시지 타입을 만들지 않고 기존 메시지를 확장한 설계라, 여기서
    깨지면 재접속 복구 경로까지 함께 흔들린다."""
    participants = generate_sample_participants(30, seed=7)
    draw = fairness.compute_draw("rt-test", participants, draw_count=3, seed="transition-seed")
    session = Session(
        session_id="rt-test",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)
    await main_module.run_racing_sequence("rt-test", 0, 300.0)

    revealed = {m["round"]: m for m in messages if m["type"] == "round_revealed"}

    # R1: 팀별 생존 카트 수 -- 합계가 통과자 수와 정확히 일치해야 한다
    survivors_r1 = revealed[1]["survivors_by_department"]
    assert sum(survivors_r1.values()) == len(draw.round_pass_ids[1])
    assert set(survivors_r1) == set(draw.snapshot["departments"])
    # 내림차순 정렬(무대가 그대로 그린다)
    assert list(survivors_r1.values()) == sorted(survivors_r1.values(), reverse=True)
    assert "finalists" not in revealed[1]  # 결선 명단은 R2 종료 시점에만

    # R2: 결선 진출자 등수표. 순서는 draw.ranking(확정 순위)을 따른다
    finalists = revealed[2]["finalists"]
    assert [f["participant_id"] for f in finalists] == [
        pid for pid in draw.ranking if pid in set(draw.round_pass_ids[2])
    ]
    assert all(f["department"] for f in finalists)  # 부서색 뱃지용


def test_candidate_stats_per_round_shape():
    """폰에서 후보를 고를 때 보여줄 지표(사용자 요청)가 라운드마다
    올바른 형태로 나오는지 확인한다. R1은 팀별 참가 카트 수뿐이고,
    R2부터는 직전 라운드 성적이 함께 붙는다."""
    participants = generate_sample_participants(30, seed=11)
    draw = fairness.compute_draw("cs-test", participants, draw_count=3, seed="cand-stats-seed")
    session = Session(
        session_id="cs-test",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)

    r1 = main_module._candidate_stats(session, 1)
    assert set(r1) == set(draw.snapshot["departments"])
    # R1은 아직 아무도 탈락하지 않았으므로 전체 인원과 합이 같다
    assert sum(v["karts"] for v in r1.values()) == len(participants)
    assert all("prev_rank" not in v for v in r1.values())

    r2 = main_module._candidate_stats(session, 2)
    assert sum(v["karts"] for v in r2.values()) == len(draw.round_pass_ids[1])
    assert all(v["prev_rank"] is not None for v in r2.values())

    r3 = main_module._candidate_stats(session, 3)
    assert set(r3) == set(draw.round_pass_ids[2])
    assert [v["prev_rank"] for v in r3.values()] == list(range(1, len(r3) + 1))

    # 커밋(추첨)이 아직 없으면 조용히 빈 지표 -- 프런트가 후보만 보여준다
    empty = Session(
        session_id="cs-empty",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    assert main_module._candidate_stats(empty, 1) == {}


@pytest.mark.asyncio
async def test_final_round_ends_after_countdown_from_first_crossing(monkeypatch):
    """결선은 1위가 결승선을 넘은 시점부터 5초가 지나면 구간 시간이 남아
    있어도 끝난다(2026-08-08 사용자 요청: "카운트다운 끝나면 결과 발표").

    R1/R2의 컷오프와 달리 이건 **레이스를 끝내는 규칙**이라, 마지막 틱에
    race_over 신호가 실려 나가고 진행률이 1.0에 도달하기 전에 멈춘다."""
    participants = generate_sample_participants(24, seed=21)
    draw = fairness.compute_draw("r3-cut", participants, draw_count=3, seed="r3-cutoff-seed")
    session = Session(
        session_id="r3-cut",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.0)
    # 창(5초)이 구간 안에서 실제로 닫히도록 넉넉한 구간 길이를 준다.
    monkeypatch.setattr(main_module, "RACE_COUNTDOWN_SECONDS", 0.0)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    # loop.time()을 틱마다 일정하게 흐르는 가짜 시계로 대체해, 실제로 초를
    # 기다리지 않고도 "1위 통과 + 5초"를 재현한다.
    loop = asyncio.get_running_loop()
    ticks = iter([i * 0.5 for i in range(2000)])
    monkeypatch.setattr(loop, "time", lambda: next(ticks))

    await main_module._run_race_phase(draw, 3, duration_seconds=120.0, session_id="r3-cut")

    r3 = [m for m in messages if m["type"] == "race_tick"]
    assert r3, "결선 틱이 하나도 나오지 않았습니다"
    last = r3[-1]

    assert last["race_over"] is True, "마지막 틱에 결선 종료 신호가 있어야 한다"
    assert last["progress_ratio"] < 1.0, "구간 시간을 다 쓰지 않고 조기 종료해야 한다"
    assert last["cutoff_window_seconds"] == fairness.R3_CUTOFF_WINDOW_SECONDS

    # 종료 신호는 마지막 틱에만 붙는다(중간에 미리 켜지면 안 된다)
    assert [m for m in r3 if m["race_over"]] == [last]

    # 1위가 결승선을 넘은 시점과 종료 시점의 간격이 창 길이와 맞는지
    crossed = [m for m in r3 if any(p >= m["pass_line"] for p in m["positions"].values())]
    assert crossed, "결선에서 아무도 결승선을 넘지 못했습니다"
    first_ratio = crossed[0]["progress_ratio"]
    race_seconds = main_module._post_countdown_race_seconds(120.0)
    expected = first_ratio + fairness.R3_CUTOFF_WINDOW_SECONDS / race_seconds
    assert last["progress_ratio"] == pytest.approx(expected, abs=0.02)


@pytest.mark.asyncio
async def test_race_tick_cache_is_cleared_when_sequence_completes(monkeypatch):
    """진행이 끝나면 마지막 레이스 틱 캐시를 비워야 한다.

    안 비우면 모바일 "내 카트 현황"이 시상식 내내(그리고 행사가 끝난
    뒤로도) 마지막 틱을 그대로 보여준다. 결선이 1위 통과 + 5초로 조기
    종료되면서부터는 진행률 1.0 전 스냅샷이 얼어붙어 "⏳ 진행 중 /
    통과선까지 87%"처럼 아직 달리는 것처럼 보인다."""
    participants = generate_sample_participants(20, seed=31)
    draw = fairness.compute_draw("cache-clear", participants, draw_count=3, seed="cache-clear-seed")
    session = Session(
        session_id="cache-clear",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    async def fake_broadcast(message, sender=None, roles=None):
        pass

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("cache-clear", 0, 300.0)

    assert main_module.latest_race_tick is None
    # 캐시가 비었으면 폰의 레이스 현황 카드는 조용히 사라진다(포인트 순위만 남음)
    assert main_module._my_race_status(participants[0].id) is None
    assert main_module._my_department_rank(participants[0].id) is None


@pytest.mark.asyncio
async def test_final_round_does_not_end_instantly_when_everyone_qualifies(monkeypatch):
    """당첨자 수와 결선 진출자 수가 같으면 결선 통과선이 -0.01(전원 통과)이
    된다. 이때 "1위가 결승선을 넘었는가" 판정이 **출발 전부터 참**이라,
    결선 카운트다운이 곧바로 걸려 레이스가 5초 만에 끝나 버렸다.

    resolve_finalist_count(10) == 10처럼 당첨자가 10명 이상이면 항상 이
    상태가 되므로 드문 경우가 아니다."""
    assert fairness.resolve_finalist_count(10) == 10, "전제: 당첨 10명이면 결선도 10명"

    participants = generate_sample_participants(40, seed=41)
    draw = fairness.compute_draw("all-win", participants, draw_count=10, seed="all-win-seed")
    session = Session(
        session_id="all-win",
        participants=participants,
        draw_count=10,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(main_module, "RACE_COUNTDOWN_SECONDS", 0.0)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    loop = asyncio.get_running_loop()
    ticks = iter([i * 0.5 for i in range(4000)])
    monkeypatch.setattr(loop, "time", lambda: next(ticks))

    await main_module._run_race_phase(draw, 3, duration_seconds=120.0, session_id="all-win")

    r3 = [m for m in messages if m["type"] == "race_tick"]
    last = r3[-1]
    assert last["pass_line"] <= 0, "전제: 전원 통과라 통과선이 축퇴값"
    # 결승선이 없으므로 조기 종료도, 컷오프 UI도 없어야 한다
    assert last["race_over"] is False
    assert last["progress_ratio"] == pytest.approx(1.0), "구간을 끝까지 달려야 한다"
    assert last["has_finish_line"] is False
    assert last["candidate_count"] is None
    assert last["cutoff_window_seconds"] is None


def test_obstacles_spread_over_whole_track_when_everyone_passes():
    """전원 통과라 결승선이 없으면 장애물은 트랙 전체에 펼쳐져야 한다.

    예전에는 hazard_line이 0.25로 하한을 둬서, 장애물 10개가 전부 트랙 앞
    25%에 뭉치고 나머지 75%가 텅 비었다(참가자 100명 이하 행사에서 항상)."""
    from app import race as race_module

    assert race_module.has_finish_line(-0.01) is False
    assert race_module.has_finish_line(1.01) is False
    assert race_module.has_finish_line(0.68) is True

    layout = race_module.obstacle_layout("spread-seed", 1, -0.01)
    positions = sorted(h["at_ratio"] for h in layout)
    lo, hi = race_module.HAZARD_SPAN
    assert positions[0] >= lo - 1e-9
    assert positions[-1] <= hi + 1e-9
    # 트랙 뒷부분(50% 이후)에도 장애물이 있어야 한다
    assert any(p > 0.5 for p in positions), f"장애물이 앞쪽에만 뭉쳐 있습니다: {positions}"


# ---------------------------------------------------------------------------
# 진행 중인 레이스와 세션 조작(재추첨/새 세션/초기화)의 상호작용
# ---------------------------------------------------------------------------

_LONG_RACE_SEGMENTS = [
    RunbookSegment(phase="opening", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r1", duration_seconds=3.0, is_selection_window=False),
    RunbookSegment(phase="score_r1_select_r2", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r2", duration_seconds=0.05, is_selection_window=False),
    RunbookSegment(phase="score_r2_select_r3", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r3", duration_seconds=0.05, is_selection_window=False),
    RunbookSegment(phase="final_announce", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="verify", duration_seconds=0.01, is_selection_window=False),
]


def _racing_session(session_id: str, seed: str):
    participants = generate_sample_participants(30, seed=1)
    draw = fairness.compute_draw(session_id, participants, draw_count=3, seed=seed)
    session = Session(
        session_id=session_id,
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
        predictions_enabled=False,
    )
    session.draws.append(draw)
    return session, draw


@pytest.mark.asyncio
async def test_redraw_during_a_race_stops_it_instead_of_announcing_the_old_winners(monkeypatch):
    """**재추첨을 누르면 진행 중이던 레이스가 즉시 멈춰야 한다.**

    재추첨은 session_id를 그대로 두고 draw만 덧붙이기 때문에, 런북의 구간
    경계 검사(session_id 비교)로는 걸러지지 않는다. 옛 런북을 끊지 않으면
    끝까지 완주하면서 `fairness.reveal`까지 실행해 **재추첨 전 당첨자**를
    최종 발표해버린다 -- 진행자는 다시 뽑았다고 생각하는데 화면에는 옛
    결과가 뜨는, 행사에서 가장 곤란한 종류의 사고다.

    실제로 재현됐던 회귀라 테스트로 고정한다(재추첨 후에도 33틱이 더 나가고
    옛 draw의 winners가 그대로 발표됐다).
    """
    session, old_draw = _racing_session("sess-redraw", "seed-old")
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kw: list(_LONG_RACE_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(main_module, "RACE_COUNTDOWN_SECONDS", 0.01)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    task = asyncio.create_task(main_module.run_racing_sequence("sess-redraw", 0, 300.0))
    main_module.active_race_tasks["sess-redraw"] = task
    await asyncio.sleep(0.4)  # R1 레이스 구간 한복판
    assert any(m["type"] == "race_tick" for m in messages), "전제: 레이스가 실제로 돌고 있어야 함"

    await main_module.redraw(main_module.RedrawRequest(exclude_previous_winners=False))

    ticks_at_redraw = sum(1 for m in messages if m["type"] == "race_tick")
    await asyncio.sleep(0.5)
    ticks_after = sum(1 for m in messages if m["type"] == "race_tick")

    assert ticks_after == ticks_at_redraw, "재추첨 후에도 옛 레이스가 계속 틱을 쏘고 있습니다"
    assert task.done() or task.cancelled()
    # 옛 추첨이 최종 발표까지 가지 못했어야 한다
    assert not any(m["type"] == "revealed" for m in messages)
    assert not any(m["type"] == "prize_winners" for m in messages)
    assert old_draw.revealed is False
    # 다음 레이스를 바로 시작할 수 있어야 한다(태스크 슬롯이 비었는지)
    assert "sess-redraw" not in main_module.active_race_tasks
    main_module.store.clear()


@pytest.mark.asyncio
async def test_new_session_during_a_race_stops_the_old_one_immediately(monkeypatch):
    """새 세션을 만들면 옛 레이스가 **즉시** 멈춰야 한다.

    session_id가 바뀌므로 런북은 결국 스스로 멈추지만, 그 검사는 **구간
    경계에서만** 이뤄진다 -- 레이스 구간 하나가 기본 95초라 그동안 새 세션
    화면 위로 옛 레이스 틱이 계속 흘러간다."""
    session, _ = _racing_session("sess-old", "seed-1")
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kw: list(_LONG_RACE_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(main_module, "RACE_COUNTDOWN_SECONDS", 0.01)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    task = asyncio.create_task(main_module.run_racing_sequence("sess-old", 0, 300.0))
    main_module.active_race_tasks["sess-old"] = task
    await asyncio.sleep(0.4)
    assert any(m["type"] == "race_tick" for m in messages)

    await main_module.create_session(
        main_module.CreateSessionRequest(
            participants=[p.to_dict() for p in generate_sample_participants(30, seed=2)],
            draw_count=3,
            mode="racing",
            total_seconds=300.0,
            predictions_enabled=False,
        )
    )

    ticks_at_switch = sum(1 for m in messages if m["type"] == "race_tick")
    await asyncio.sleep(0.4)
    assert sum(1 for m in messages if m["type"] == "race_tick") == ticks_at_switch
    # 폰 "내 카트 현황"이 옛 레이스의 마지막 틱에 얼어붙어 있으면 안 된다
    assert main_module.latest_race_tick is None
    main_module.store.clear()


@pytest.mark.asyncio
async def test_race_tick_sends_each_kart_collision_lane(monkeypatch):
    """**무대가 카트를 자기 충돌 레인에 그리려면 서버가 레인을 내려줘야 한다.**

    이걸 안 보내면 화면은 자체 해시(laneFor, 5~28차선)로 카트를 그리는데,
    그건 서버가 판정에 쓰는 레인(0~7)과 아무 관계가 없다. 그 상태에서는
    "3번 레인에서 맞았다"고 판정된 카트가 화면에서는 7번 레인에 있어서,
    장애물을 스쳐 지나가는데 갑자기 느려지고(사용자 피드백: "부딪힌 즉시
    효과가 나타나지 않아") 정작 장애물 위를 지나간 옆 카트는 멀쩡해
    "주변 카트가 같이 영향받는" 것처럼 보였다. 실측으로 충돌한 카트가
    화면에서 장애물과 겹쳐 보이는 비율이 약 12%에 불과했다.

    또한 **지금 감속 중인 카트의 효과는 반드시 그 카트 레인에 놓인
    장애물 종류**여야 한다 -- 어긋나면 화면과 판정이 다시 갈라진다.
    """
    session, draw = _racing_session("sess-lane", "seed-lane")
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kw: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages: list[dict] = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)
    await main_module.run_racing_sequence("sess-lane", 0, 300.0)

    ticks = [m for m in messages if m["type"] == "race_tick"]
    assert ticks, "레이스 틱이 하나도 없습니다"

    from app import race as race_module

    checked = 0
    for tick in ticks:
        lanes = tick.get("lanes")
        assert lanes, "race_tick에 카트별 레인(lanes)이 없습니다"
        # 서버가 판정에 쓰는 값과 정확히 같아야 한다
        for pid, lane in lanes.items():
            assert lane == race_module.lane_for(draw.seed, pid, tick["round"])
            assert 0 <= lane < race_module.LANE_COUNT

        lanes_with_obstacle: dict[int, set[str]] = {}
        for obstacle in tick.get("obstacles") or []:
            lanes_with_obstacle.setdefault(obstacle["lane"], set()).add(obstacle["type"])
        for pid, effect in (tick.get("effects") or {}).items():
            assert effect["type"] in lanes_with_obstacle.get(lanes[pid], set()), (
                f"{pid}가 자기 레인({lanes[pid]})에 없는 장애물({effect['type']})에 맞고 있습니다"
            )
            checked += 1

    assert checked > 0, "감속 중인 카트가 한 번도 없어 검증이 무의미합니다"
    main_module.store.clear()
