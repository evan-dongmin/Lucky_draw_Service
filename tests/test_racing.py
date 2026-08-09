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
