import pytest

from app import fairness
from app import main as main_module
from app.director import RunbookSegment
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


@pytest.mark.asyncio
async def test_racing_with_predictions_scores_across_all_rounds(monkeypatch):
    participants = generate_sample_participants(40, seed=3)
    draw = fairness.compute_draw("pred-race", participants, draw_count=3, seed="pred-race-seed")
    session = Session(
        session_id="pred-race",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=True,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)
    main_module.prediction_engine.reset()
    main_module.predict_tokens.clear()

    # R1 창은 main.py의 commit 흐름을 거치지 않았으므로 수동으로 개방
    department_names = list(draw.snapshot["departments"].keys())
    main_module.prediction_engine.open_round(1, department_names)

    predictor_id = participants[0].id
    main_module.prediction_engine.set_allocation(predictor_id, {1: 20, 2: 30, 3: 50})
    # 1등 통과율 부서를 실제로 맞히도록 설정(적중 유도)
    top_dept = predictions_top_department(draw, round_index=1)
    main_module.prediction_engine.set_target(predictor_id, 1, top_dept)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("pred-race", 0, 300.0)

    window_events = [m for m in messages if m["type"] == "prediction_window"]
    opened_rounds = [m["round"] for m in window_events if m["state"] == "open"]
    locked_rounds = [m["round"] for m in window_events if m["state"] == "locked"]
    # 라운드 1 개방은 (실제 서비스에서는 commit_draw가 담당하므로) 이 테스트에서
    # run_racing_sequence 호출 전에 직접 열어뒀다 -- 그래서 브로드캐스트 캡처에는
    # 2·3라운드 개방만 잡힌다. 잠금은 세 라운드 모두 run_racing_sequence 책임이다.
    assert opened_rounds == [2, 3]
    assert locked_rounds == [1, 2, 3]

    leaderboard_events = [m for m in messages if m["type"] == "prediction_leaderboard"]
    assert len(leaderboard_events) == 3  # 라운드마다 한 번씩 채점 후 브로드캐스트

    card = main_module.prediction_engine.cards[predictor_id]
    assert card.locked == {1: True, 2: True, 3: True}
    assert card.gain[1] > 0  # 1라운드는 적중하도록 설정했으므로 점수를 얻어야 함
    assert card.score >= card.gain[1]


def predictions_top_department(draw, round_index: int) -> str:
    from app.predictions import top_k_by_rate

    rates = draw.department_pass_rate[round_index]
    k = 2 if round_index == 1 else 1
    return sorted(top_k_by_rate(rates, k))[0]


@pytest.mark.asyncio
async def test_predictions_disabled_produces_no_prediction_events(monkeypatch):
    """가산성 검증: 예측 게임이 꺼져 있으면 prediction_* 이벤트가 전혀 없어야 한다."""
    participants = generate_sample_participants(30, seed=4)
    draw = fairness.compute_draw("no-pred-race", participants, draw_count=2, seed="no-pred-seed")
    session = Session(
        session_id="no-pred-race",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=False,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("no-pred-race", 0, 300.0)

    prediction_events = [m for m in messages if m["type"].startswith("prediction_")]
    assert prediction_events == []
    assert [m["type"] for m in messages].count("racing_complete") == 1
