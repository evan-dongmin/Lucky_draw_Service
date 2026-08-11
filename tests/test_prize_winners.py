"""_compute_prize_winners: 실제 경품 당첨자를 예측 게임 최종 리더보드에서
뽑는 로직. 레이스/공정성(fairness.py, draw.winners)은 이 파일에서 전혀
건드리지 않는다 -- 그 위에 얹는 별도 결정 단계이기 때문이다."""

import asyncio
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


def _make_session(predictions_enabled: bool, draw_count: int = 3, count: int = 40) -> tuple[Session, object]:
    participants = generate_sample_participants(count, seed=3)
    draw = fairness.compute_draw(
        f"prize-{predictions_enabled}", participants, draw_count=draw_count, seed="prize-seed"
    )
    fairness.reveal(draw)
    session = Session(
        session_id="prize-session",
        participants=participants,
        draw_count=draw_count,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=predictions_enabled,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    return session, draw


def test_predictions_disabled_falls_back_to_race_winners():
    session, draw = _make_session(predictions_enabled=False)
    ids, basis, scores, ranks, notes = main_module._compute_prize_winners(session, draw)
    assert basis == "race"
    assert ids == list(draw.winners)


def test_prediction_leaderboard_can_diverge_from_race_winners():
    """핵심 시나리오: 레이스 순위와 무관하게 예측 점수를 제일 많이 모은
    사람이 실제 경품 당첨자가 돼야 한다(레이스 1등이라도 점수가 낮으면
    탈락). 전원에게 레이스 순위와 정반대인 점수를 줘서 확실히 검증한다."""
    session, draw = _make_session(predictions_enabled=True)
    main_module.prediction_engine.reset()

    for i, pid in enumerate(draw.ranking):
        card = main_module.prediction_engine.get_or_create_card(pid)
        card.score = i + 1  # 레이스 순위가 뒤일수록 예측 점수가 높음

    ids, basis, scores, ranks, notes = main_module._compute_prize_winners(session, draw)
    assert basis == "prediction"
    expected = list(reversed(draw.ranking))[: len(draw.winners)]
    assert ids == expected
    assert set(ids).isdisjoint(draw.winners)  # 레이스 당첨자는 전원 점수 최하위라 실제 당첨에서 탈락


def test_prize_winner_count_shrinks_when_fewer_participants_engaged():
    """당첨 인원 N보다 예측 게임에 참여한 사람이 적으면(모바일 온보딩을
    안 한 사람이 많음) 그만큼만 실제 당첨자가 나온다 -- 에러 없이 조용히
    부족분만큼 줄어들어야 한다."""
    session, draw = _make_session(predictions_enabled=True, draw_count=5)
    main_module.prediction_engine.reset()
    only_two = list(draw.ranking)[:2]
    for pid in only_two:
        main_module.prediction_engine.get_or_create_card(pid)

    ids, basis, scores, ranks, notes = main_module._compute_prize_winners(session, draw)
    assert basis == "prediction"
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
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)
    main_module.prediction_engine.reset()
    main_module.predict_tokens.clear()
    department_names = list(draw.snapshot["departments"].keys())
    main_module.prediction_engine.open_round(1, department_names)

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
    # 라운드 3 최종 채점(리더보드 갱신) 이후에 당첨자가 정해져야 한다
    leaderboard_positions = [i for i, m in enumerate(messages) if m["type"] == "prediction_leaderboard"]
    assert leaderboard_positions
    assert types_seen.index("prize_winners") > leaderboard_positions[-1]

    prize_msg = next(m for m in messages if m["type"] == "prize_winners")
    assert prize_msg["basis"] == "prediction"
    assert draw.prize_winners == prize_msg["winners"]
    assert draw.prize_basis == "prediction"


def test_my_prize_result_before_and_after_announcement():
    """참가자 폰이 "내가 됐나?"를 폴링만으로도 알 수 있어야 한다.

    WS 이벤트(prize_winners)만으로 처리하면 발표 순간 화면이 꺼져 있었거나
    새로고침한 사람은 결과를 영영 못 본다."""
    participants = generate_sample_participants(20, seed=5)
    session = Session(
        session_id="prize-me",
        participants=participants,
        draw_count=3,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    draw = fairness.compute_draw("prize-me", participants, draw_count=3, seed="prize-me-seed")
    session.draws.append(draw)

    # 발표 전에는 아무것도 알려주지 않는다(리빌 전 결과 유출 방지)
    assert main_module._my_prize_result(session, participants[0].id) is None

    fairness.reveal(draw)
    draw.prize_winners = list(draw.winners)
    draw.prize_basis = "race"

    winner = draw.winners[0]
    loser = next(p.id for p in participants if p.id not in draw.winners)

    won = main_module._my_prize_result(session, winner)
    assert won["announced"] is True
    assert won["is_winner"] is True
    assert won["winner_rank"] == 1
    assert won["winner_count"] == len(draw.winners)
    assert won["basis"] == "race"

    lost = main_module._my_prize_result(session, loser)
    assert lost["announced"] is True
    assert lost["is_winner"] is False
    assert lost["winner_rank"] is None
    # 당첨자 수는 떨어진 사람에게도 알려준다("상위 N명이 당첨" 안내용)
    assert lost["winner_count"] == len(draw.winners)


def test_no_session_or_draw_yields_no_prize_result():
    """커밋 전(추첨 자체가 없는) 세션에서도 조용히 None -- 폴링이 죽으면 안 된다."""
    participants = generate_sample_participants(5, seed=6)
    empty = Session(
        session_id="prize-empty",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    assert main_module._my_prize_result(empty, participants[0].id) is None


def test_prize_basis_values_are_limited_to_the_two_known_labels():
    """basis 문자열은 무대(PRIZE_BASIS_LABEL)와 폰(PRIZE_BASIS_NOTE) 양쪽에서
    안내 문구를 찾는 키다. 서버가 그 두 값 말고 다른 걸 내보내면 화면에
    아무 설명도 안 뜨는(조용히 비는) 버그가 된다 -- 실제로 폰 쪽 키가
    'confidence'로 어긋나 있던 것을 이 검증을 넣으며 잡았다."""
    import pathlib
    import re

    allowed = {"race", "prediction"}

    participants = generate_sample_participants(20, seed=7)
    session = Session(
        session_id="basis",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=False,
        created_at="2026-01-01T00:00:00Z",
    )
    draw = fairness.compute_draw("basis", participants, draw_count=2, seed="basis-seed")
    session.draws.append(draw)
    fairness.reveal(draw)

    _, basis, *_ = main_module._compute_prize_winners(session, draw)
    assert basis in allowed

    session.predictions_enabled = True
    main_module.prediction_engine.reset()
    main_module.prediction_engine.enroll_all(main_module._department_by_pid(draw))
    _, basis_pred, *_ = main_module._compute_prize_winners(session, draw)
    assert basis_pred in allowed

    # 프런트 양쪽이 같은 키를 알고 있어야 한다
    root = pathlib.Path(__file__).resolve().parent.parent / "static"
    for filename, table in (("stage.js", "PRIZE_BASIS_LABEL"), ("mobile.js", "PRIZE_BASIS_NOTE")):
        source = (root / filename).read_text(encoding="utf-8")
        block = re.search(rf"const {table} = \{{(.*?)\}};", source, re.S)
        assert block, f"{filename}에서 {table}을 찾지 못했습니다"
        keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
        assert keys <= allowed, f"{filename}의 {table}에 모르는 basis 키가 있습니다: {keys - allowed}"


def test_predictions_disabled_session_still_gets_race_status_and_prize(monkeypatch):
    """예측 게임이 꺼진 순수 레이싱 세션의 폰도 레이스 현황과 당첨 결과를
    받아야 한다.

    예전에는 /api/predict/me가 predictions_enabled=False면 곧바로 돌아가서,
    이 모드의 참가자는 레이스 내내 아무것도 못 보고 당첨 여부조차 알 수
    없었다 -- 정작 이 모드에서 basis="race"로 당첨자가 정해지는데도."""
    participants = generate_sample_participants(20, seed=8)
    session = Session(
        session_id="no-pred-me",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        predictions_enabled=False,
        created_at="2026-01-01T00:00:00Z",
    )
    draw = fairness.compute_draw("no-pred-me", participants, draw_count=2, seed="no-pred-me-seed")
    session.draws.append(draw)
    fairness.reveal(draw)
    draw.prize_winners = list(draw.winners)
    draw.prize_basis = "race"
    main_module.store.set_session(session)

    pid = draw.winners[0]
    token = "tok-no-pred"
    main_module.predict_tokens[token] = pid
    try:
        payload = asyncio.run(main_module.predict_me(token))
    finally:
        main_module.predict_tokens.pop(token, None)

    assert payload["predictions_enabled"] is False
    # 예측과 무관한 정보는 이 경로에서도 내려와야 한다
    assert "prize" in payload and payload["prize"]["is_winner"] is True
    assert payload["prize"]["basis"] == "race"
    assert "race_status" in payload
    assert "department_rank" in payload
