import pytest

from app.predictions import (
    FINAL_WIN_POINTS,
    FINISH_POINTS,
    FLOOR_RATIO,
    MIN_ALLOC,
    RANK_RATIOS,
    TEAM_RANK_POINTS,
    TOTAL_ALLOC,
    PredictionEngine,
    PredictionError,
    rank_ratio,
    rank_targets_by_rate,
)


def _default_alloc(a=34, b=33, c=33):
    return {1: a, 2: b, 3: c}


# ---------------------------------------------------------------------------
# 순위 계산 / 배점 비율
# ---------------------------------------------------------------------------


def test_rank_targets_by_rate_breaks_ties_by_name():
    rates = {"B팀": 0.5, "A팀": 0.5, "C팀": 0.9}
    assert rank_targets_by_rate(rates) == ["C팀", "A팀", "B팀"]


def test_rank_ratio_is_monotonically_decreasing_and_floors_out():
    ratios = [rank_ratio(r) for r in range(1, len(RANK_RATIOS) + 1)]
    assert ratios == RANK_RATIOS
    assert all(a > b for a, b in zip(ratios, ratios[1:]))
    # 순위표 밖은 전부 참여 보상으로 수렴한다(0이 되지 않는 것이 핵심)
    assert rank_ratio(len(RANK_RATIOS) + 1) == FLOOR_RATIO
    assert rank_ratio(99) == FLOOR_RATIO
    assert FLOOR_RATIO > 0


# ---------------------------------------------------------------------------
# 확신도 배분 -- R1·R2에만 하한, R3는 몰아주기 허용
# ---------------------------------------------------------------------------


def test_new_card_has_default_even_allocation_summing_to_100():
    engine = PredictionEngine()
    card = engine.get_or_create_card("P1")
    assert sum(card.alloc.values()) == TOTAL_ALLOC


def test_set_allocation_rejects_wrong_total():
    engine = PredictionEngine()
    with pytest.raises(PredictionError):
        engine.set_allocation("P1", {1: 50, 2: 30, 3: 30})  # 합계 110


def test_set_allocation_rejects_below_minimum_in_early_rounds():
    engine = PredictionEngine()
    with pytest.raises(PredictionError):
        engine.set_allocation("P1", {1: 5, 2: 45, 3: 50})  # 1라운드가 최소 10 미만


def test_round3_has_no_minimum_so_it_can_be_zero():
    """R3에는 하한이 없다 -- 앞 라운드에 몰아쓰는 전략도 가능해야 한다."""
    engine = PredictionEngine()
    card = engine.set_allocation("P1", {1: 50, 2: 50, 3: 0})
    assert card.alloc[3] == 0


def test_max_all_in_on_round3_is_allowed():
    """R1·R2 최소치만 남기고 나머지를 R3에 전부 몰아줄 수 있어야 한다(역전용)."""
    engine = PredictionEngine()
    card = engine.set_allocation("P1", {1: MIN_ALLOC, 2: MIN_ALLOC, 3: TOTAL_ALLOC - 2 * MIN_ALLOC})
    assert card.alloc[3] == 80


def test_set_allocation_accepts_valid_distribution():
    engine = PredictionEngine()
    card = engine.set_allocation("P1", _default_alloc(20, 30, 50))
    assert card.alloc == {1: 20, 2: 30, 3: 50}


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


# ---------------------------------------------------------------------------
# 대상 선택 창
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 전원 자동 참가 + 미선택 시 기본값(R1·R2 자기 부서 / R3 무작위)
# ---------------------------------------------------------------------------


def test_enroll_all_creates_a_card_for_every_listed_participant():
    """모바일로 참여하지 않은 사람도 명단에 있으면 카드가 생겨야 한다
    -- 그래야 아무것도 안 해도 경품 대상에 남는다."""
    engine = PredictionEngine()
    engine.enroll_all({"P1": "개발팀", "P2": "영업팀", "P3": "개발팀"})
    assert set(engine.cards) == {"P1", "P2", "P3"}
    assert engine.department_by_pid["P3"] == "개발팀"


@pytest.mark.parametrize("round_index", [1, 2])
def test_early_rounds_default_to_own_department(round_index):
    engine = PredictionEngine()
    engine.enroll_all({"P1": "개발팀", "P2": "영업팀"})
    engine.open_round(round_index, ["개발팀", "영업팀", "인사팀"])
    engine.lock_round(round_index, seed="autopick-seed")

    assert engine.cards["P1"].target[round_index] == "개발팀"
    assert engine.cards["P2"].target[round_index] == "영업팀"
    assert engine.cards["P1"].is_auto[round_index] is True


def test_explicit_choice_wins_over_own_department_default():
    engine = PredictionEngine()
    engine.enroll_all({"P1": "개발팀"})
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "영업팀")
    engine.lock_round(1, seed="seed")
    assert engine.cards["P1"].target[1] == "영업팀"
    assert engine.cards["P1"].is_auto[1] is False


def test_round3_defaults_to_random_not_own_department():
    """R3 대상은 결선 진출자 개인이라 '자기 팀'이라는 개념이 없다 --
    부서명이 후보에 섞여 있더라도 시드 파생 무작위로 배정돼야 한다."""
    engine = PredictionEngine()
    engine.enroll_all({"P1": "개발팀"})
    engine.open_round(3, ["FIN-1", "FIN-2", "FIN-3", "개발팀"])
    engine.lock_round(3, seed="r3-seed")

    target = engine.cards["P1"].target[3]
    assert target in ["FIN-1", "FIN-2", "FIN-3", "개발팀"]
    assert engine.cards["P1"].is_auto[3] is True


def test_default_falls_back_to_random_when_own_department_not_a_candidate():
    engine = PredictionEngine()
    engine.enroll_all({"P1": "사라진팀"})
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.lock_round(1, seed="seed")
    assert engine.cards["P1"].target[1] in ["개발팀", "영업팀"]


def test_autopick_is_deterministic_given_same_seed():
    def build():
        engine = PredictionEngine()
        engine.open_round(3, ["A", "B", "C", "D", "E"])
        engine.get_or_create_card("P1")
        engine.lock_round(3, seed="same-seed")
        return engine.cards["P1"].target[3]

    assert build() == build()


# ---------------------------------------------------------------------------
# 실시간 분포
# ---------------------------------------------------------------------------


def test_live_distribution_empty_when_nobody_has_chosen():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    assert engine.live_distribution(1) == {}


def test_live_distribution_reflects_explicit_choices_before_lock():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.set_target("P2", 1, "개발팀")
    engine.set_target("P3", 1, "영업팀")

    dist = engine.live_distribution(1)
    assert dist["개발팀"] == pytest.approx(2 / 3)
    assert dist["영업팀"] == pytest.approx(1 / 3)


def test_live_distribution_excludes_autopicked_cards_and_matches_post_lock_share():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.get_or_create_card("P2")  # 선택하지 않음 -> 잠금 시 자동배정될 예정

    before_lock = engine.live_distribution(1)
    assert before_lock == {"개발팀": 1.0}

    engine.lock_round(1, seed="seed")
    share = engine.score_round(1, ["개발팀", "영업팀"])
    assert share == before_lock  # 창이 닫히는 순간 값과 채점 시 분포가 일치해야 함


# ---------------------------------------------------------------------------
# 순위 차등 채점 -- 이 게임의 핵심
# ---------------------------------------------------------------------------


def test_gain_decreases_with_rank_but_never_reaches_zero():
    """1위를 맞힌 사람이 가장 많이 받되, 꼴찌를 고른 사람도 0점은 아니어야
    한다(사용자 요청: 참여하면 적더라도 보상)."""
    engine = PredictionEngine()
    ranked = ["1위팀", "2위팀", "3위팀", "4위팀", "5위팀", "6위팀"]
    for i, pid in enumerate(["P1", "P2", "P3", "P4", "P5", "P6"]):
        engine.set_allocation(pid, _default_alloc(100 - 2 * MIN_ALLOC, MIN_ALLOC, MIN_ALLOC))
        engine.get_or_create_card(pid)
    engine.open_round(1, ranked)
    for pid, target in zip(["P1", "P2", "P3", "P4", "P5", "P6"], ranked):
        engine.set_target(pid, 1, target)
    engine.lock_round(1, seed="seed")
    engine.score_round(1, ranked)

    gains = [engine.cards[pid].gain[1] for pid in ["P1", "P2", "P3", "P4", "P5", "P6"]]
    assert gains == sorted(gains, reverse=True)
    assert all(g > 0 for g in gains)
    assert gains[0] > gains[-1] * 5  # 1위 적중과 꼴찌 사이에 충분한 격차


def test_target_outside_ranked_list_still_gets_participation_floor():
    """순위표에 없는 대상(예: 통과율 집계가 비어 있는 경우)도 0점이 아니다."""
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(80, 10, 10))
    engine.open_round(1, ["A", "B"])
    engine.set_target("P1", 1, "B")
    engine.lock_round(1, seed="seed")
    engine.score_round(1, ["A"])  # B는 순위 목록에 없음

    assert engine.cards["P1"].gain[1] == int(80 * 1.0 * FLOOR_RATIO)


def test_minority_bonus_applies_only_to_the_exact_top_pick():
    """소수파 보너스는 1위를 정확히 맞혔을 때만 -- 틀렸는데 아무도 안 골라서
    더 받는 일이 없어야 한다."""
    engine = PredictionEngine()
    for pid in ["P1", "P2", "P3"]:
        engine.set_allocation(pid, _default_alloc(80, 10, 10))
    engine.open_round(1, ["A", "B"])
    engine.set_target("P1", 1, "A")  # 소수파(1/3) -- 1위 적중
    engine.set_target("P2", 1, "B")
    engine.set_target("P3", 1, "B")  # 다수파 -- 2위
    engine.lock_round(1, seed="seed")
    engine.score_round(1, ["A", "B"])

    # P1: 80 * 1.0 * 1.0 * (1 + (1 - 1/3)) = 133
    assert engine.cards["P1"].gain[1] == int(80 * 1.0 * (1 + (1 - 1 / 3)))
    # P2/P3: 보너스 없이 2위 비율만 -- 80 * 0.6 = 48
    assert engine.cards["P2"].gain[1] == int(80 * RANK_RATIOS[1])
    assert engine.cards["P3"].gain[1] == engine.cards["P2"].gain[1]


def test_bonus_is_neutral_when_no_explicit_choosers():
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.lock_round(1, seed="seed")  # 아무도 명시적으로 선택하지 않음
    card = engine.cards["P1"]
    card.target[1] = "개발팀"  # 강제로 적중 상황을 만들어 보너스 배수만 검증

    engine.score_round(1, ["개발팀", "영업팀"])
    # bonus=1.0이므로 gain = alloc(20) * weight(1.0) * ratio(1.0) = 20
    assert card.gain[1] == 20


def test_score_round_is_idempotent_recomputation():
    """같은 입력으로 재계산해도 점수가 중복 가산되지 않아야 한다
    (서버 재시작 후 재계산 시나리오). 성과 점수까지 포함해서 검증한다."""
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.set_target("P1", 1, "개발팀")
    engine.lock_round(1, seed="seed")

    kwargs = {"passed_ids": {"P1"}, "ranked_dept_ids": [{"P1"}]}
    engine.score_round(1, ["개발팀", "영업팀"], **kwargs)
    first = engine.cards["P1"].score
    engine.score_round(1, ["개발팀", "영업팀"], **kwargs)

    assert engine.cards["P1"].score == first


def test_score_never_negative_and_participation_always_pays():
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc(10, 10, 80))
    for r, candidates in [(1, ["A", "B"]), (2, ["A", "B"]), (3, ["X", "Y"])]:
        engine.open_round(r, candidates)
        engine.lock_round(r, seed=f"seed-{r}")  # 항상 미선택 -> 자동배정
        engine.score_round(r, [])  # 순위 정보가 전혀 없는 극단 케이스
        assert engine.cards["P1"].score >= 0
    # 순위표가 비어도 참여 보상은 들어간다 -- 아무도 0점으로 방치되지 않는다
    assert engine.cards["P1"].score > 0


def test_round3_all_in_beats_two_early_round_wins():
    """R3 몰아주기로 앞 두 라운드를 모두 1위로 맞힌 사람을 뒤집을 수 있어야
    한다(사용자 요청: 마지막 라운드 역전 드라마)."""
    engine = PredictionEngine()
    engine.set_allocation("STEADY", {1: 45, 2: 45, 3: 10})
    engine.set_allocation("ALLIN", {1: MIN_ALLOC, 2: MIN_ALLOC, 3: 80})

    for r, candidates in [(1, ["A", "B"]), (2, ["A", "B"])]:
        engine.open_round(r, candidates)
        engine.set_target("STEADY", r, "A")  # 1위 적중
        engine.set_target("ALLIN", r, "B")  # 최하위
        engine.lock_round(r, seed=f"seed-{r}")
        engine.score_round(r, ["A", "B"])

    assert engine.cards["STEADY"].score > engine.cards["ALLIN"].score  # 여기까지는 STEADY 우세

    engine.open_round(3, ["X", "Y"])
    engine.set_target("STEADY", 3, "Y")
    engine.set_target("ALLIN", 3, "X")  # 결승 1위 적중
    engine.lock_round(3, seed="seed-3")
    engine.score_round(3, ["X", "Y"])

    assert engine.cards["ALLIN"].score > engine.cards["STEADY"].score


# ---------------------------------------------------------------------------
# 성과 점수(예측 적중과 무관하게 쌓이는 몫)
# ---------------------------------------------------------------------------


def test_performance_points_are_added_on_top_of_prediction_points():
    engine = PredictionEngine()
    engine.enroll_all({"P1": "개발팀", "P2": "영업팀"})
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.lock_round(1, seed="seed")
    engine.score_round(
        1,
        ["개발팀", "영업팀"],
        passed_ids={"P1"},  # P1만 통과
        ranked_dept_ids=[{"P1"}, {"P2"}],  # 개발팀이 통과율 1위
    )

    p1 = engine.cards["P1"].rewards[1]
    assert p1["finish"] == FINISH_POINTS
    assert p1["team_bonus"] == TEAM_RANK_POINTS[0]
    assert p1["team_rank"] == 1
    assert p1["total"] == p1["predict"] + FINISH_POINTS + TEAM_RANK_POINTS[0]

    # 탈락자는 성과 점수가 없지만 예측 점수(참여 보상 포함)는 그대로 받는다
    p2 = engine.cards["P2"].rewards[1]
    assert "finish" not in p2
    assert p2["total"] == p2["predict"] > 0


def test_team_below_third_place_gets_finish_points_only():
    engine = PredictionEngine()
    engine.enroll_all({f"P{i}": f"T{i}" for i in range(1, 6)})
    engine.open_round(1, [f"T{i}" for i in range(1, 6)])
    engine.lock_round(1, seed="seed")
    engine.score_round(
        1,
        [f"T{i}" for i in range(1, 6)],
        passed_ids={"P5"},
        ranked_dept_ids=[{f"P{i}"} for i in range(1, 6)],  # P5의 팀은 5위
    )
    reward = engine.cards["P5"].rewards[1]
    assert reward["finish"] == FINISH_POINTS
    assert "team_bonus" not in reward


def test_final_winner_points_awarded_in_round3():
    engine = PredictionEngine()
    engine.enroll_all({"P1": "개발팀"})
    engine.open_round(3, ["P1", "P2"])
    engine.lock_round(3, seed="seed")
    engine.score_round(3, ["P1", "P2"], final_winner_ids={"P1"})

    assert engine.cards["P1"].rewards[3]["final"] == FINAL_WIN_POINTS


# ---------------------------------------------------------------------------
# 영속화
# ---------------------------------------------------------------------------


def test_engine_to_dict_load_dict_round_trip_preserves_full_state():
    """서버 재시작 복구 시나리오: 스냅샷 -> 새 인스턴스로 복원해도
    카드·점수·라운드 상태가 완전히 동일해야 한다(재계산 없이 그대로 복원)."""
    engine = PredictionEngine()
    engine.enroll_all({"P1": "A"})
    engine.set_allocation("P1", _default_alloc(20, 30, 50))
    engine.open_round(1, ["A", "B"])
    engine.set_target("P1", 1, "A")
    engine.lock_round(1, seed="restore-seed")
    engine.score_round(1, ["A", "B"], passed_ids={"P1"}, ranked_dept_ids=[{"P1"}])
    engine.open_round(2, ["A", "B"])

    snapshot = engine.to_dict()

    restored = PredictionEngine()
    restored.load_dict(snapshot)

    assert restored.cards["P1"].to_dict() == engine.cards["P1"].to_dict()
    assert restored.round_state == engine.round_state
    assert restored.round_candidates == engine.round_candidates
    assert restored.round_share == engine.round_share
    assert restored.department_by_pid == engine.department_by_pid


def test_load_dict_restores_in_place_keeping_same_instance():
    """모듈 싱글턴 패턴에서 다른 코드가 들고 있는 참조가 깨지지 않아야 한다."""
    engine = PredictionEngine()
    engine.set_allocation("P1", _default_alloc())
    reference = engine  # 다른 모듈이 들고 있을 법한 참조

    snapshot = engine.to_dict()
    engine.reset()
    assert "P1" not in engine.cards

    engine.load_dict(snapshot)
    assert reference is engine  # 동일 인스턴스
    assert "P1" in reference.cards


def test_leaderboard_sorted_descending_with_stable_tiebreak():
    engine = PredictionEngine()
    for pid in ["P3", "P1", "P2"]:
        engine.set_allocation(pid, _default_alloc())
    engine.open_round(1, ["A"])  # 후보가 하나뿐이라 셋 다 동일하게 자동배정되어 동점 처리됨
    engine.lock_round(1, "seed")
    engine.score_round(1, ["A"])

    top = engine.leaderboard(top_n=10)
    scores = {c.participant_id: c.score for c in top}
    assert len(set(scores.values())) == 1  # 셋 다 동점
    assert [c.participant_id for c in top] == ["P1", "P2", "P3"]  # 동점자는 id 오름차순
