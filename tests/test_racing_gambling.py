import pytest

from app import fairness
from app import main as main_module
from app.director import RunbookSegment
from app.gambling import STARTING_BALANCE
from app.models import Session
from app.roster import generate_sample_participants

TINY_SEGMENTS = [
    RunbookSegment(phase="opening", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="r1_lock", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r1", duration_seconds=0.02, is_selection_window=False),
    RunbookSegment(phase="score_r1_select_r2", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r2", duration_seconds=0.02, is_selection_window=False),
    RunbookSegment(phase="score_r2_select_r3", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r3", duration_seconds=0.02, is_selection_window=False),
    RunbookSegment(phase="final_announce", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="verify", duration_seconds=0.01, is_selection_window=False),
]


def _top_department(draw, round_index: int) -> str:
    from app.predictions import top_k_by_rate

    rates = draw.department_pass_rate[round_index]
    k = 2 if round_index == 1 else 1
    return sorted(top_k_by_rate(rates, k))[0]


@pytest.mark.asyncio
async def test_racing_with_gambling_settles_bets_across_all_rounds(monkeypatch):
    participants = generate_sample_participants(40, seed=5)
    draw = fairness.compute_draw("bet-race", participants, draw_count=3, seed="bet-race-seed")
    session = Session(
        session_id="bet-race",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=True,
        prediction_mode="gambling",
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)
    main_module.gambling_engine.reset()
    main_module.predict_tokens.clear()

    # R1 창은 main.py의 commit 흐름을 거치지 않았으므로 수동으로 개방
    department_names = list(draw.snapshot["departments"].keys())
    main_module.gambling_engine.open_round(1, department_names)

    bettor_id = participants[0].id
    top_dept = _top_department(draw, round_index=1)
    main_module.gambling_engine.place_bet(bettor_id, 1, top_dept, 150)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("bet-race", 0, 300.0)

    window_events = [m for m in messages if m["type"] == "prediction_window"]
    opened_rounds = [m["round"] for m in window_events if m["state"] == "open"]
    locked_rounds = [m["round"] for m in window_events if m["state"] == "locked"]
    assert opened_rounds == [2, 3]
    assert locked_rounds == [1, 2, 3]
    assert all(m["mode"] == "gambling" for m in window_events)

    result_events = [m for m in messages if m["type"] == "gambling_result"]
    assert [m["round"] for m in result_events] == [1, 2, 3]

    leaderboard_events = [m for m in messages if m["type"] == "prediction_leaderboard"]
    assert len(leaderboard_events) == 3
    assert all(m["mode"] == "gambling" for m in leaderboard_events)

    card = main_module.gambling_engine.cards[bettor_id]
    assert card.locked == {1: True, 2: True, 3: True}
    # 1라운드는 적중하도록 걸었으므로 잔액이 시작값보다 늘어나 있어야 한다
    assert card.balance > STARTING_BALANCE - 150


@pytest.mark.asyncio
async def test_racing_gambling_completes_with_zero_bettors(monkeypatch):
    """아무도 베팅하지 않아도(전원 미접속) 레이싱·추첨은 정상 완주해야
    한다 -- 갬블링도 확신도 배분과 마찬가지로 완전히 가산적 계층이다."""
    participants = generate_sample_participants(25, seed=11)
    draw = fairness.compute_draw("empty-bet-race", participants, draw_count=2, seed="empty-bet-seed")
    session = Session(
        session_id="empty-bet-race",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=True,
        prediction_mode="gambling",
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)
    main_module.gambling_engine.reset()
    main_module.predict_tokens.clear()
    department_names = list(draw.snapshot["departments"].keys())
    main_module.gambling_engine.open_round(1, department_names)
    # 의도적으로 아무도 베팅하지 않음

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("empty-bet-race", 0, 300.0)

    types_seen = [m["type"] for m in messages]
    assert types_seen.count("racing_complete") == 1
    assert draw.revealed is True
    assert len(draw.winners) == 2
    assert main_module.gambling_engine.cards == {}
