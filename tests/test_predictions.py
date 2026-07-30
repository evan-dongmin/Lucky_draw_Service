import pytest

from app.predictions import (
    MIN_ALLOC,
    TOTAL_ALLOC,
    PredictionEngine,
    PredictionError,
    top_k_by_rate,
)


def _default_alloc(a=34, b=33, c=33):
    return {1: a, 2: b, 3: c}


def test_top_k_by_rate_breaks_ties_by_name():
    rates = {"B팀": 0.5, "A팀": 0.5, "C팀": 0.9}
    assert top_k_by_rate(rates, 2) == {"C팀", "A팀"}


def test_new_card_has_default_even_allocation_summing_to_100():
    engine = PredictionEngine()
    card = engine.get_or_create_card("P1")
    assert sum(card.alloc.values()) == TOTAL_ALLOC
    assert all(v >= MIN_ALLOC for v in card.alloc.values())


def test_set_allocation_rejects_wrong_total():
    engine = PredictionEngine()
    with pytest.raises(PredictionError):
        engine.set_allocation("P1", {1: 50, 2: 30, 3: 30})  # 합계 110


def test_set_allocation_rejects_below_minimum():
    engine = PredictionEngine()
    with pytest.raises(PredictionError):
        engine.set_allocation("P1", {1: 5, 2: 45, 3: 50})  # 1라운드가 최소 10 미만


def test_set_allocation_accepts_valid_distribution():
    engine = PredictionEngine()
    card = engine.set_allocation("P1", _default_alloc(20, 30, 50))
    assert card.alloc == {1: 20, 2: 30, 3: 50}


def test_target_cannot_be_set_before_window_opens():
    engine = PredictionEngine()
    with pytest.raises(PredictionError):
        engine.set_target("P1", 1, "개발팀")


def test_target_can_be_set_and_changed_while_window_open():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.set_target("P1", 1, "영업팀")  # last-write-wins
    assert engine.cards["P1"].target[1] == "영업팀"
    assert engine.cards["P1"].is_auto[1] is False


def test_target_rejected_if_not_in_candidates():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    with pytest.raises(PredictionError):
        engine.set_target("P1", 1, "없는부서")


def test_target_cannot_be_changed_after_lock():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.lock_round(1, seed="test-seed")
    with pytest.raises(PredictionError):
        engine.set_target("P1", 1, "영업팀")


def test_lock_round_autopicks_for_participants_without_explicit_choice():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀", "인사팀"])
    engine.get_or_create_card("P1")  # target 미설정
    engine.lock_round(1, seed="autopick-seed")
    card = engine.cards["P1"]
    assert card.target[1] in ["개발팀", "영업팀", "인사팀"]
    assert card.is_auto[1] is True
    assert card.locked[1] is True


def test_autopick_is_deterministic_given_same_seed():
    engine1 = PredictionEngine()
    engine1.open_round(1, ["A", "B", "C", "D", "E"])
    engine1.get_or_create_card("P1")
    engine1.lock_round(1, seed="same-seed")

    engine2 = PredictionEngine()
    engine2.open_round(1, ["A", "B", "C", "D", "E"])
    engine2.get_or_create_card("P1")
    engine2.lock_round(1, seed="same-seed")

    assert engine1.cards["P1"].target[1] == engine2.cards["P1"].target[1]


def test_allocation_locked_round_value_preserved_when_reallocating_others():
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀"])
    engine.lock_round(1, seed="seed")  # 1라운드 잠금

    # 1라운드 값(20)은 유지한 채 2·3라운드만 재배분
    engine.set_allocation("P1", {1: 20, 2: 40, 3: 40})
    assert engine.cards["P1"].alloc == {1: 20, 2: 40, 3: 40}

    with pytest.raises(PredictionError):
        engine.set_allocation("P1", {1: 25, 2: 35, 3: 40})  # 잠긴 라운드 값 변경 시도


def test_score_round_awards_gain_only_to_hitters():
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.set_allocation("P2", _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.set_target("P2", 1, "영업팀")
    engine.lock_round(1, seed="seed")

    engine.score_round(1, hit_set={"개발팀"})

    assert engine.cards["P1"].score > 0
    assert engine.cards["P2"].score == 0


def test_score_uses_only_explicit_choosers_for_distribution():
    engine = PredictionEngine()
    for pid in ["P1", "P2", "P3"]:
        engine.set_allocation(pid, _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.set_target("P2", 1, "개발팀")
    # P3는 선택하지 않음 -> 잠금 시 자동배정(무작위) 처리, 분포 계산에서 제외되어야 함
    engine.lock_round(1, seed="seed")

    share = engine.score_round(1, hit_set={"개발팀"})

    # 분포는 명시적으로 고른 P1,P2만으로 계산되어야 한다(개발팀=100%)
    assert share.get("개발팀") == pytest.approx(1.0)


def test_bonus_is_neutral_when_no_explicit_choosers():
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀", "영업팀"])
    # 아무도 명시적으로 선택하지 않음
    engine.lock_round(1, seed="seed")
    card = engine.cards["P1"]
    card.target[1] = "개발팀"  # 강제로 적중 상황을 만들어 보너스 배수만 검증
    card.is_auto[1] = True

    engine.score_round(1, hit_set={"개발팀"})
    # bonus=1.0이므로 gain = alloc(20) * weight(1.0) * 1.0 = 20
    assert card.gain[1] == 20


def test_score_round_is_idempotent_recomputation():
    """같은 hit_set으로 재계산해도 점수가 중복 가산되지 않아야 한다
    (서버 재시작 후 재계산 시나리오)."""
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.lock_round(1, seed="seed")

    engine.score_round(1, hit_set={"개발팀"})
    score_after_first = engine.cards["P1"].score

    engine.score_round(1, hit_set={"개발팀"})  # 재계산
    score_after_second = engine.cards["P1"].score

    assert score_after_first == score_after_second


def test_score_never_negative_across_rounds():
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(10, 10, 80))
    for r, candidates in [(1, ["A", "B"]), (2, ["A", "B"]), (3, ["X", "Y"])]:
        engine.open_round(r, candidates)
        engine.lock_round(r, seed=f"seed-{r}")  # 항상 미선택 -> 자동배정
        engine.score_round(r, hit_set=set())  # 아무도 적중 못 함
        assert engine.cards["P1"].score >= 0
    assert engine.cards["P1"].score == 0


def test_round_weight_progression_allows_late_comeback():
    """R1·R2를 다 틀려도 R3 적중만으로 유의미한 점수를 얻어야 한다(역전 가능성)."""
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(10, 10, 80))
    engine.open_round(1, ["A", "B"])
    engine.set_target("P1", 1, "A")
    engine.lock_round(1, "seed")
    engine.score_round(1, hit_set={"B"})  # 틀림

    engine.open_round(2, ["A", "B"])
    engine.set_target("P1", 2, "A")
    engine.lock_round(2, "seed")
    engine.score_round(2, hit_set={"B"})  # 틀림

    engine.open_round(3, ["X", "Y"])
    engine.set_target("P1", 3, "X")
    engine.lock_round(3, "seed")
    engine.score_round(3, hit_set={"X"})  # 적중

    assert engine.cards["P1"].score > 0
    assert engine.cards["P1"].gain[3] > engine.cards["P1"].gain[1]


def test_leaderboard_sorted_descending_with_stable_tiebreak():
    engine = PredictionEngine()
    engine.set_allocation("P3", _default_alloc())
    engine.set_allocation("P1", _default_alloc())
    engine.set_allocation("P2", _default_alloc())
    engine.open_round(1, ["A"])  # 후보가 하나뿐이라 셋 다 동일하게 자동배정되어 동점 처리됨
    engine.lock_round(1, "seed")
    engine.score_round(1, hit_set={"A"})

    top = engine.leaderboard(top_n=10)
    scores = {c.participant_id: c.score for c in top}
    assert len(set(scores.values())) == 1  # 셋 다 동점
    assert [c.participant_id for c in top] == ["P1", "P2", "P3"]  # 동점자는 id 오름차순
