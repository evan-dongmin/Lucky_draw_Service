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
    from app.predictions import rank_targets_by_rate

    return rank_targets_by_rate(draw.department_pass_rate[round_index])[0]


@pytest.mark.asyncio
async def test_mobile_race_status_helpers_after_race(monkeypatch):
    """모바일 "내 카트 현황" 계산(_my_race_status/_my_department_rank/
    rank_of)이 레이스가 도는 동안 합리적인 값을 돌려주는지 확인한다.

    R3는 부서 표시가 없다는 기존 규칙(_department_denom_sets)도 그대로
    지켜져야 한다. **런북 전체가 끝난 뒤에는 캐시가 비워져야** 하므로
    (그래야 시상식 중에 낡은 "진행 중" 카드가 안 남는다) 여기서는
    결선 구간만 돌려서 진행 중 상태를 검사한다."""
    participants = generate_sample_participants(40, seed=9)
    draw = fairness.compute_draw("mobile-status", participants, draw_count=3, seed="mobile-status-seed")
    session = Session(
        session_id="mobile-status",
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
    main_module.latest_race_tick = None

    department_names = list(draw.snapshot["departments"].keys())
    # 실제 서비스에서는 commit 흐름(_open_round_1)이 enroll_all을 호출해
    # 명단 전원에게 카드를 만든다 -- 여기서도 같은 순서로 재현해야
    # rank_of()가 참가자 카드를 찾을 수 있다.
    main_module.prediction_engine.enroll_all(main_module._department_by_pid(draw))
    main_module.prediction_engine.open_round(1, department_names)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    async def fake_broadcast(message, sender=None, roles=None):
        pass

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    # 결선 구간만 직접 돌려 "레이스가 진행 중인" 상태의 캐시를 만든다.
    # (run_racing_sequence를 끝까지 돌리면 완료 시점에 캐시가 비워진다 --
    #  test_race_tick_cache_is_cleared_when_sequence_completes 참고.)
    await main_module._run_race_phase(draw, 3, duration_seconds=0.05, session_id="mobile-status")

    assert main_module.latest_race_tick is not None
    assert main_module.latest_race_tick["round"] == 3

    pid = draw.round_pass_ids[3][0]  # 결선까지 살아남은 참가자 한 명
    status = main_module._my_race_status(pid)
    assert status is not None
    assert status["round"] == 3
    assert 1 <= status["rank"] <= status["total"]
    assert isinstance(status["passed"], bool)
    assert 0 <= status["progress_to_pass_pct"] <= 100

    assert main_module._my_race_status("no-such-participant") is None
    assert main_module._my_department_rank(pid) is None  # R3는 부서 표시가 없음

    # enroll_all로 명단 전원 카드가 만들어져 있으므로 채점 전에도 순위가 나온다
    rank = main_module.prediction_engine.rank_of(pid)
    assert rank is not None and rank >= 1


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


@pytest.mark.asyncio
async def test_racing_completes_with_zero_mobile_participation(monkeypatch):
    """장애 리허설: 모바일 참여자가 한 명도 없어도(전원 미접속/이탈) 레이싱과
    추첨은 정상 완주해야 한다 -- 예측 게임은 완전히 가산적 계층이다."""
    participants = generate_sample_participants(25, seed=9)
    draw = fairness.compute_draw("empty-pred-race", participants, draw_count=2, seed="empty-pred-seed")
    session = Session(
        session_id="empty-pred-race",
        participants=participants,
        draw_count=2,
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
    # 의도적으로 아무도 join/choose 하지 않음 (cards가 완전히 빈 상태)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("empty-pred-race", 0, 300.0)

    types_seen = [m["type"] for m in messages]
    assert types_seen.count("racing_complete") == 1
    assert draw.revealed is True
    assert len(draw.winners) == 2
    assert main_module.prediction_engine.cards == {}  # 참여자가 없었으므로 카드도 없음


@pytest.mark.asyncio
async def test_non_mobile_participants_still_score_and_stay_prize_eligible(monkeypatch):
    """사용자 요청의 핵심: 폰을 들지 않은 사람도 명단에 있기만 하면
    R1·R2는 자기 부서가 자동 선택되고, 순위 차등 채점 덕에 점수가 쌓여
    경품 대상에 남아야 한다(예전에는 카드 자체가 없어 통째로 제외됐다)."""
    participants = generate_sample_participants(40, seed=11)
    draw = fairness.compute_draw("no-phone", participants, draw_count=3, seed="no-phone-seed")
    session = Session(
        session_id="no-phone",
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

    # commit 흐름과 동일하게 전원 등록 + R1 개방 (아무도 join하지 않는다)
    department_names = list(draw.snapshot["departments"].keys())
    await main_module._open_round_1(session, draw, department_names)

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.01)

    async def fake_broadcast(message, sender=None, roles=None):
        pass

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    await main_module.run_racing_sequence("no-phone", 0, 300.0)

    cards = main_module.prediction_engine.cards
    assert len(cards) == len(participants)  # 전원에게 카드가 있다

    dept_by_pid = main_module._department_by_pid(draw)
    for card in cards.values():
        # R1·R2는 자기 부서가 자동 선택됐고, R3만 무작위다
        assert card.target[1] == dept_by_pid[card.participant_id]
        assert card.target[2] == dept_by_pid[card.participant_id]
        assert card.is_auto == {1: True, 2: True, 3: True}
        assert card.score > 0  # 참여 보상 덕분에 누구도 0점이 아니다

    # 폰을 안 든 사람만 있어도 경품 당첨자가 정원만큼 나온다
    assert draw.prize_basis == "prediction"
    assert len(draw.prize_winners) == 3
