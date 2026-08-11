"""예측 리더보드 동점 처리(2026-08-11, 사용자 요청).

250명이 고르는 선택지는 부서 5~8개 + 결선 진출자 5~10명뿐이라 **동점은 매
게임 반드시 생긴다**(실제 게임에서 상위 5명 중 4명이 같은 점수였다).
예전에는 사번 오름차순으로 갈랐는데, 무대에서 설명할 수 없는 기준이었다.
지금은 배점이 가장 큰 3라운드부터 "얼마나 잘 맞혔는지"로 가른다.
"""

from app import predictions
from app.predictions import PredictionEngine


def _card(engine, pid, score, hits, manual_rounds=()):
    """hits = {round: 적중 등수(0이면 순위 밖)}"""
    card = engine.get_or_create_card(pid)
    card.score = score
    for round_index, hit_rank in hits.items():
        card.rewards[round_index] = {"hit_rank": hit_rank}
    for round_index in (1, 2, 3):
        card.is_auto[round_index] = round_index not in manual_rounds
        card.target[round_index] = f"t{round_index}"
    return card


def test_same_score_is_broken_by_round3_hit_first():
    """총점이 같으면 **3라운드**를 더 잘 맞힌 사람이 앞선다. 배점이 3배라
    가장 크게 흔드는 라운드이기 때문이다."""
    engine = PredictionEngine()
    _card(engine, "P002", 1830, {1: 1, 2: 1, 3: 4})
    _card(engine, "P001", 1830, {1: 5, 2: 5, 3: 1})

    order = [c.participant_id for c in engine.leaderboard(10)]
    assert order == ["P001", "P002"], "R1·R2를 다 맞혀도 R3 적중이 앞서야 한다"


def test_round2_breaks_tie_when_round3_is_equal():
    engine = PredictionEngine()
    _card(engine, "P001", 900, {1: 1, 2: 4, 3: 2})
    _card(engine, "P002", 900, {1: 9, 2: 2, 3: 2})

    order = [c.participant_id for c in engine.leaderboard(10)]
    assert order == ["P002", "P001"]


def test_missed_prediction_sorts_behind_any_hit():
    """적중 실패(hit_rank=0)는 항상 뒤로. 0을 그대로 쓰면 '0등'이 되어
    1등 적중보다 앞서는 치명적 역전이 난다."""
    engine = PredictionEngine()
    _card(engine, "P001", 500, {3: 0})
    _card(engine, "P002", 500, {3: 8})

    order = [c.participant_id for c in engine.leaderboard(10)]
    assert order == ["P002", "P001"]


def test_manual_selection_breaks_tie_when_all_hits_equal():
    """세 라운드 적중이 모두 같으면 직접 고른 사람이 앞선다(폰을 든 사람 우대)."""
    engine = PredictionEngine()
    _card(engine, "P001", 700, {1: 2, 2: 2, 3: 2})
    _card(engine, "P002", 700, {1: 2, 2: 2, 3: 2}, manual_rounds=(1, 3))

    order = [c.participant_id for c in engine.leaderboard(10)]
    assert order == ["P002", "P001"]


def test_full_tie_shares_the_same_display_rank():
    """모든 기준이 같으면 진짜 공동 등수다. 표준 경쟁 등수라 다음 사람은
    인원수만큼 건너뛴다(공동 2등이 둘이면 다음은 4등)."""
    engine = PredictionEngine()
    _card(engine, "P001", 999, {1: 1, 2: 1, 3: 1})
    _card(engine, "P002", 500, {1: 3, 2: 3, 3: 3})
    _card(engine, "P003", 500, {1: 3, 2: 3, 3: 3})
    _card(engine, "P004", 100, {1: 9, 2: 9, 3: 9})

    rows = engine.ranked_leaderboard(10)
    by_id = {r["participant_id"]: r for r in rows}
    assert by_id["P001"]["rank"] == 1
    assert by_id["P002"]["rank"] == 2
    assert by_id["P003"]["rank"] == 2, "완전 동점이면 같은 등수"
    assert by_id["P004"]["rank"] == 4, "공동 2등이 둘이면 다음은 4등"
    assert by_id["P002"]["tiebreak_note"] == "완전 동점"


def test_tiebreak_note_names_the_criterion_that_decided():
    """같은 점수인데 순서가 갈렸으면 근거가 화면에 나와야 한다 -- 안 보이면
    관객이 반드시 의심한다(같은 점수인데 누구는 받고 누구는 못 받는다)."""
    engine = PredictionEngine()
    _card(engine, "P001", 1830, {1: 1, 2: 1, 3: 1})
    _card(engine, "P002", 1830, {1: 1, 2: 1, 3: 5})

    rows = {r["participant_id"]: r for r in engine.ranked_leaderboard(10)}
    assert rows["P001"]["tiebreak_note"] == "3R 적중 1등"
    assert rows["P002"]["tiebreak_note"] == "3R 적중 5등"
    assert rows["P001"]["rank"] == 1
    assert rows["P002"]["rank"] == 2, "갈렸으면 공동 등수가 아니다"


def test_solo_score_gets_no_tiebreak_note():
    """동점자가 없으면 근거 문구도 없어야 한다(불필요한 잡음 방지)."""
    engine = PredictionEngine()
    _card(engine, "P001", 900, {3: 1})
    _card(engine, "P002", 800, {3: 2})

    rows = engine.ranked_leaderboard(10)
    assert all(r["tiebreak_note"] == "" for r in rows)


def test_rank_of_uses_the_same_order_as_leaderboard():
    """모바일 '내 순위'와 무대 리더보드가 서로 다른 규칙을 쓰면 안 된다."""
    engine = PredictionEngine()
    _card(engine, "P001", 500, {3: 7})
    _card(engine, "P002", 500, {3: 1})
    _card(engine, "P003", 900, {3: 3})

    order = [c.participant_id for c in engine.leaderboard(10)]
    for idx, pid in enumerate(order, start=1):
        assert engine.rank_of(pid) == idx


def test_ranking_key_is_deterministic_for_identical_cards():
    """완전 동점끼리는 사번으로 순서를 고정한다 -- 서버를 재시작해도 같은
    순서가 나와야 재계산 가능성이 유지된다."""
    engine = PredictionEngine()
    _card(engine, "P009", 400, {1: 2, 2: 2, 3: 2})
    _card(engine, "P003", 400, {1: 2, 2: 2, 3: 2})

    assert [c.participant_id for c in engine.leaderboard(10)] == ["P003", "P009"]
    assert predictions.hit_rank_of(engine.get_or_create_card("P003"), 3) == 2
