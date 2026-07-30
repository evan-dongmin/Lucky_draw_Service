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
