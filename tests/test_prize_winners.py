"""_compute_prize_winners: 실제 경품 당첨자를 예측/갬블링 최종 리더보드에서
뽑는 로직. 레이스/공정성(fairness.py, draw.winners)은 이 파일에서 전혀
건드리지 않는다 -- 그 위에 얹는 별도 결정 단계이기 때문이다."""

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


def _make_session(mode: str, predictions_enabled: bool, draw_count: int = 3, count: int = 40) -> tuple[Session, object]:
    participants = generate_sample_participants(count, seed=3)
    draw = fairness.compute_draw(f"prize-{mode}-{predictions_enabled}", participants, draw_count=draw_count, seed="prize-seed")
    fairness.reveal(draw)
    session = Session(
        session_id="prize-session",
        participants=participants,
        draw_count=draw_count,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=predictions_enabled,
        prediction_mode=mode,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    return session, draw


def test_predictions_disabled_falls_back_to_race_winners():
    session, draw = _make_session("confidence", predictions_enabled=False)
    ids, basis = main_module._compute_prize_winners(session, draw)
    assert basis == "race"
    assert ids == list(draw.winners)


def test_gambling_leaderboard_can_diverge_from_race_winners():
    """핵심 시나리오: 레이스 순위와 무관하게 사이버머니를 제일 많이 모은
    사람이 실제 경품 당첨자가 돼야 한다(레이스 1등이라도 잔액이 낮으면
    탈락). 전원에게 레이스 순위와 정반대인 잔액을 줘서 확실히 검증한다."""
    session, draw = _make_session("gambling", predictions_enabled=True)
    main_module.gambling_engine.reset()

    n = len(draw.ranking)
    for i, pid in enumerate(draw.ranking):
        card = main_module.gambling_engine.get_or_create_card(pid)
        card.balance = i + 1  # 레이스 순위가 뒤일수록(꼴찌에 가까울수록) 잔액이 높음

    ids, basis = main_module._compute_prize_winners(session, draw)
    assert basis == "gambling"
    expected = list(reversed(draw.ranking))[: len(draw.winners)]
    assert ids == expected
    assert set(ids).isdisjoint(draw.winners)  # 레이스 당첨자는 전원 잔액 최하위라 실제 당첨에서 탈락


def test_confidence_leaderboard_can_diverge_from_race_winners():
    session, draw = _make_session("confidence", predictions_enabled=True)
    main_module.prediction_engine.reset()

    for i, pid in enumerate(draw.ranking):
        card = main_module.prediction_engine.get_or_create_card(pid)
        card.score = i + 1  # 레이스 순위가 뒤일수록 예측 점수가 높음

    ids, basis = main_module._compute_prize_winners(session, draw)
    assert basis == "confidence"
    expected = list(reversed(draw.ranking))[: len(draw.winners)]
    assert ids == expected
    assert set(ids).isdisjoint(draw.winners)


def test_prize_winner_count_shrinks_when_fewer_participants_engaged():
    """당첨 인원 N보다 예측/갬블링에 참여한 사람이 적으면(모바일 온보딩을
    안 한 사람이 많음) 그만큼만 실제 당첨자가 나온다 -- 에러 없이 조용히
    부족분만큼 줄어들어야 한다."""
    session, draw = _make_session("gambling", predictions_enabled=True, draw_count=5)
    main_module.gambling_engine.reset()
    only_two = list(draw.ranking)[:2]
    for pid in only_two:
        main_module.gambling_engine.get_or_create_card(pid)

    ids, basis = main_module._compute_prize_winners(session, draw)
    assert basis == "gambling"
    assert len(ids) == 2
    assert set(ids) == set(only_two)


def test_reveal_roulette_mode_sets_prize_winners_to_race_result(client):
    sample = client.get("/api/roster/sample", params={"count": 30}).json()
    client.post(
        "/api/session",
        json={"participants": sample["participants"], "draw_count": 3, "mode": "roulette"},
    )
    client.post("/api/draw/commit")
    revealed = client.post("/api/draw/reveal", json={}).json()

    assert revealed["prize_basis"] == "race"
    assert revealed["prize_winners"] == revealed["winners"]


@pytest.mark.asyncio
async def test_racing_sequence_broadcasts_prize_winners_after_final_scoring(monkeypatch):
    """final_announce 단계에서: revealed -> (라운드3 최종 채점) -> prize_winners
    순서로 브로드캐스트되는지, 그리고 draw 객체에 실제로 저장되는지 확인한다."""
    participants = generate_sample_participants(30, seed=9)
    draw = fairness.compute_draw("prize-e2e", participants, draw_count=3, seed="prize-e2e-seed")
    session = Session(
        session_id="prize-e2e",
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
    department_names = list(draw.snapshot["departments"].keys())
    main_module.gambling_engine.open_round(1, department_names)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("prize-e2e", 0, 300.0)

    types_seen = [m["type"] for m in messages]
    assert "prize_winners" in types_seen
    assert types_seen.index("prize_winners") > types_seen.index("revealed")
    # 라운드 3 최종 채점(gambling_result) 이후에 당첨자가 정해져야 한다
    round3_result_positions = [i for i, m in enumerate(messages) if m["type"] == "gambling_result" and m.get("round") == 3]
    assert round3_result_positions
    assert types_seen.index("prize_winners") > round3_result_positions[0]

    prize_msg = next(m for m in messages if m["type"] == "prize_winners")
    assert prize_msg["basis"] == "gambling"
    assert draw.prize_winners == prize_msg["winners"]
    assert draw.prize_basis == "gambling"
