import pytest

from app.predictions import (
    FINAL_WIN_POINTS,
    FINISH_POINTS,
    FLOOR_RATIO,
    RANK_RATIOS,
    ROUND_BASE_POINTS,
    TEAM_RANK_POINTS,
    PredictionEngine,
    PredictionError,
    rank_ratio,
    rank_targets_by_rate,
)


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
# 대상 선택 창 -- 라운드마다 하나씩 고른다(확신도 배분 없음, 2026-08-07 단순화)
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
# 순위 차등 채점 -- 이 게임의 핵심. 라운드마다 동일한 ROUND_BASE_POINTS를
# 걸고 겨루며(예전의 개인별 확신도 배분은 없음), 배점 = ROUND_BASE_POINTS
# x ROUND_WEIGHTS[라운드] x rank_ratio(내 순위).
# ---------------------------------------------------------------------------


def test_gain_decreases_with_rank_but_never_reaches_zero():
    """1위를 맞힌 사람이 가장 많이 받되, 꼴찌를 고른 사람도 0점은 아니어야
    한다(사용자 요청: 참여하면 적더라도 보상)."""
    engine = PredictionEngine()
    ranked = ["1위팀", "2위팀", "3위팀", "4위팀", "5위팀", "6위팀"]
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
    engine.open_round(1, ["A", "B"])
    engine.set_target("P1", 1, "B")
    engine.lock_round(1, seed="seed")
    engine.score_round(1, ["A"])  # B는 순위 목록에 없음

    assert engine.cards["P1"].gain[1] == int(ROUND_BASE_POINTS * 1.0 * FLOOR_RATIO)


def test_minority_bonus_applies_only_to_the_exact_top_pick():
    """소수파 보너스는 1위를 정확히 맞혔을 때만 -- 틀렸는데 아무도 안 골라서
    더 받는 일이 없어야 한다."""
    engine = PredictionEngine()
    engine.open_round(1, ["A", "B"])
    engine.set_target("P1", 1, "A")  # 소수파(1/3) -- 1위 적중
    engine.set_target("P2", 1, "B")
    engine.set_target("P3", 1, "B")  # 다수파 -- 2위
    engine.lock_round(1, seed="seed")
    engine.score_round(1, ["A", "B"])

    # P1: 100 * 1.0 * 1.0 * (1 + (1 - 1/3)) = 166
    assert engine.cards["P1"].gain[1] == int(ROUND_BASE_POINTS * 1.0 * (1 + (1 - 1 / 3)))
    # P2/P3: 보너스 없이 2위 비율만 -- 100 * 0.6 = 60
    assert engine.cards["P2"].gain[1] == int(ROUND_BASE_POINTS * RANK_RATIOS[1])
    assert engine.cards["P3"].gain[1] == engine.cards["P2"].gain[1]


def test_bonus_is_neutral_when_no_explicit_choosers():
    engine = PredictionEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.lock_round(1, seed="seed")  # 아무도 명시적으로 선택하지 않음
    card = engine.get_or_create_card("P1")
    card.target[1] = "개발팀"  # 강제로 적중 상황을 만들어 보너스 배수만 검증

    engine.score_round(1, ["개발팀", "영업팀"])
    # bonus=1.0이므로 gain = ROUND_BASE_POINTS(100) * weight(1.0) * ratio(1.0) = 100
    assert card.gain[1] == ROUND_BASE_POINTS


def test_score_round_is_idempotent_recomputation():
    """같은 입력으로 재계산해도 점수가 중복 가산되지 않아야 한다
    (서버 재시작 후 재계산 시나리오). 성과 점수까지 포함해서 검증한다."""
    engine = PredictionEngine()
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
    engine.get_or_create_card("P1")
    for r, candidates in [(1, ["A", "B"]), (2, ["A", "B"]), (3, ["X", "Y"])]:
        engine.open_round(r, candidates)
        engine.lock_round(r, seed=f"seed-{r}")  # 항상 미선택 -> 자동배정
        engine.score_round(r, [])  # 순위 정보가 전혀 없는 극단 케이스
        assert engine.cards["P1"].score >= 0
    # 순위표가 비어도 참여 보상은 들어간다 -- 아무도 0점으로 방치되지 않는다
    assert engine.cards["P1"].score > 0


def test_round3_weight_lets_a_late_comeback_beat_an_early_leader():
    """확신도 배분이 없어져도(2026-08-07 단순화) "막판 역전" 드라마는
    ROUND_WEIGHTS만으로 그대로 유지돼야 한다 -- 앞 두 라운드를 모두
    맞힌 사람도, 결선(가중치 3배)을 통째로 놓치고 상대가 결선만 적중하면
    뒤집힐 수 있어야 한다(사용자 요청: 마지막 라운드 역전 드라마는
    확신도 없이도 유지)."""
    engine = PredictionEngine()

    for r, candidates in [(1, ["A", "B"]), (2, ["A", "B"])]:
        engine.open_round(r, candidates)
        engine.set_target("STEADY", r, "A")  # 1위 적중
        engine.set_target("COMEBACK", r, "B")  # 2위(빗나감)
        engine.lock_round(r, seed=f"seed-{r}")
        engine.score_round(r, ["A", "B"])

    assert engine.cards["STEADY"].score > engine.cards["COMEBACK"].score  # 여기까지는 STEADY 우세

    engine.open_round(3, ["X", "Y"])
    engine.set_target("STEADY", 3, "Y")  # 2위(빗나감)
    engine.set_target("COMEBACK", 3, "X")  # 결승 1위 적중
    engine.lock_round(3, seed="seed-3")
    engine.score_round(3, ["X", "Y"])

    assert engine.cards["COMEBACK"].score > engine.cards["STEADY"].score


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
    engine.get_or_create_card("P1")
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
        engine.get_or_create_card(pid)
    engine.open_round(1, ["A"])  # 후보가 하나뿐이라 셋 다 동일하게 자동배정되어 동점 처리됨
    engine.lock_round(1, "seed")
    engine.score_round(1, ["A"])

    top = engine.leaderboard(top_n=10)
    scores = {c.participant_id: c.score for c in top}
    assert len(set(scores.values())) == 1  # 셋 다 동점
    assert [c.participant_id for c in top] == ["P1", "P2", "P3"]  # 동점자는 id 오름차순


def test_rank_of_matches_leaderboard_order():
    """모바일 "포인트 순위" 표시용 rank_of()가 leaderboard()와 같은 정렬
    규칙(점수 내림차순, 동점은 id 오름차순)으로 1-based 순위를 돌려주는지
    확인한다. 카드가 없는 참가자는 None."""
    engine = PredictionEngine()
    for pid in ["P3", "P1", "P2"]:
        engine.get_or_create_card(pid)
    engine.open_round(1, ["A"])
    engine.lock_round(1, "seed")
    engine.score_round(1, ["A"])

    assert engine.rank_of("P1") == 1
    assert engine.rank_of("P2") == 2
    assert engine.rank_of("P3") == 3
    assert engine.rank_of("no-such-participant") is None


# ---------------------------------------------------------------------------
# 카트 능력에 따른 예측 점수 특성 (작업계획서 §12-3)
#
# 핵심 불변식: 능력은 **예측 점수만** 비튼다. 레이스 순위·통과 판정은
# fairness.py가 커밋된 시드로만 계산하므로 이 테스트들이 건드리는 값과
# 무관하다. 아래 테스트는 "고른 사람만, 정해진 폭만큼" 달라지는지를 본다.
# ---------------------------------------------------------------------------


def _engine_with(pids, round_index=1, candidates=("A", "B")):
    engine = PredictionEngine()
    for pid in pids:
        engine.get_or_create_card(pid)
    engine.open_round(round_index, list(candidates))
    return engine


def test_ability_multiplier_applies_only_to_chooser():
    """같은 예측을 해도 카트를 고른 사람만 배수를 받는다.

    미선택자에게 중립 1.0배를 주는 건 의도된 설계다 -- 안 고른 사람이
    우연히 유리해지면 "고르는 재미"를 주려던 취지가 뒤집힌다."""
    engine = _engine_with(["chooser", "nobody"])
    engine.set_target("chooser", 1, "A")
    engine.set_target("nobody", 1, "A")
    engine.lock_round(1, "seed")

    # nitro = R1 예측 점수 +25%
    engine.score_round(1, ["A", "B"], character_by_pid={"chooser": "nitro"})

    boosted = engine.cards["chooser"].rewards[1]["predict"]
    plain = engine.cards["nobody"].rewards[1]["predict"]
    assert boosted > plain
    assert boosted == int(plain * 1.25)
    # 능력으로 더 받은 몫이 표시용으로 남는다(모바일에서 노출)
    assert engine.cards["chooser"].rewards[1]["ability_bonus"] == boosted - plain
    assert "ability_bonus" not in engine.cards["nobody"].rewards[1]


def test_ability_only_applies_to_its_own_round():
    """rocket은 R3 전용이라 R1 점수는 그대로여야 한다."""
    engine = _engine_with(["P1", "P2"])
    engine.set_target("P1", 1, "A")
    engine.set_target("P2", 1, "A")
    engine.lock_round(1, "seed")
    engine.score_round(1, ["A", "B"], character_by_pid={"P1": "rocket"})

    assert engine.cards["P1"].rewards[1]["predict"] == engine.cards["P2"].rewards[1]["predict"]


def test_shield_doubles_only_the_participation_floor():
    """shield는 "순위표 밖"일 때만 발동한다.

    1위를 맞힌 경우에는 아무 효과가 없어야 한다 -- 안정 지향 능력이
    하이리스크 상황까지 덤으로 챙기면 다른 능력을 고를 이유가 없어진다.
    """
    # 순위표(RANK_RATIOS)보다 후보가 많아야 FLOOR_RATIO 구간이 생긴다
    candidates = [f"T{i}" for i in range(len(RANK_RATIOS) + 2)]
    ranked = list(candidates)

    engine = _engine_with(["floor_hit", "floor_plain"], candidates=candidates)
    engine.set_target("floor_hit", 1, candidates[-1])  # 꼴찌 -> 참여 보상 구간
    engine.set_target("floor_plain", 1, candidates[-1])
    engine.lock_round(1, "seed")
    engine.score_round(1, ranked, character_by_pid={"floor_hit": "shield"})

    assert engine.cards["floor_hit"].rewards[1]["predict"] == (
        engine.cards["floor_plain"].rewards[1]["predict"] * 2
    )

    # 1위 적중 시에는 shield 효과가 없다
    engine2 = _engine_with(["top_hit", "top_plain"], candidates=candidates)
    engine2.set_target("top_hit", 1, candidates[0])
    engine2.set_target("top_plain", 1, candidates[0])
    engine2.lock_round(1, "seed")
    engine2.score_round(1, ranked, character_by_pid={"top_hit": "shield"})

    assert (
        engine2.cards["top_hit"].rewards[1]["predict"]
        == engine2.cards["top_plain"].rewards[1]["predict"]
    )


def test_stardust_doubles_finish_and_wave_boosts_team_bonus():
    """성과 점수(통과/팀 순위)에 붙는 능력도 정확히 그 항목만 키운다."""
    engine = _engine_with(["star", "wave", "plain"])
    for pid in ["star", "wave", "plain"]:
        engine.set_target(pid, 1, "A")
    engine.lock_round(1, "seed")

    passed = {"star", "wave", "plain"}
    top_dept = [{"star", "wave", "plain"}]  # 전원 통과율 1위 부서 소속
    engine.score_round(
        1,
        ["A", "B"],
        passed_ids=passed,
        ranked_dept_ids=top_dept,
        character_by_pid={"star": "stardust", "wave": "wave"},
    )

    r_star = engine.cards["star"].rewards[1]
    r_wave = engine.cards["wave"].rewards[1]
    r_plain = engine.cards["plain"].rewards[1]

    assert r_star["finish"] == FINISH_POINTS * 2
    assert r_wave["finish"] == FINISH_POINTS  # stardust가 아니면 통과 점수는 그대로
    assert r_wave["team_bonus"] == int(TEAM_RANK_POINTS[0] * 1.5)
    assert r_star["team_bonus"] == TEAM_RANK_POINTS[0]
    assert r_plain["finish"] == FINISH_POINTS
    assert r_plain["team_bonus"] == TEAM_RANK_POINTS[0]


def test_unknown_character_id_is_neutral():
    """모르는 id(구버전 스냅샷 등)는 조용히 중립 처리한다 -- 채점이 죽으면 안 된다."""
    engine = _engine_with(["P1", "P2"])
    engine.set_target("P1", 1, "A")
    engine.set_target("P2", 1, "A")
    engine.lock_round(1, "seed")
    engine.score_round(1, ["A", "B"], character_by_pid={"P1": "no-such-kart"})

    assert engine.cards["P1"].rewards[1]["predict"] == engine.cards["P2"].rewards[1]["predict"]


def test_ability_scoring_stays_idempotent_on_rescore():
    """능력 배수가 붙어도 재채점이 멱등해야 한다(장애 복구 시 재계산 경로)."""
    engine = _engine_with(["P1"])
    engine.set_target("P1", 1, "A")
    engine.lock_round(1, "seed")

    engine.score_round(1, ["A", "B"], character_by_pid={"P1": "nitro"})
    once = engine.cards["P1"].score
    engine.score_round(1, ["A", "B"], character_by_pid={"P1": "nitro"})
    assert engine.cards["P1"].score == once


def test_ability_roster_and_effects_stay_in_sync():
    """로스터에 있는 카트는 전부 효과 정의와 설명 문구를 갖춰야 한다.

    화면에는 8종이 다 뜨는데 효과 표에서 빠진 카트가 있으면, 참가자는
    "설명 없는 카트"를 고르게 되고 그건 선택지가 아니라 함정이다."""
    from app import characters

    roster_ids = {c["id"] for c in characters.CHARACTER_ROSTER}
    assert roster_ids == set(characters.ABILITY_EFFECTS)
    for entry in characters.CHARACTER_ROSTER:
        assert entry["effect"], f"{entry['id']}에 효과 설명이 없습니다"
        assert entry["style"], f"{entry['id']}에 성향 설명이 없습니다"
    # 미선택자는 중립
    assert characters.effect_for(None) is characters.NEUTRAL_ABILITY


# ---------------------------------------------------------------------------
# 실시간 선택 통계 (무대·폰의 "표가 어디로 몰리나 / 어디가 소수파인가")
# ---------------------------------------------------------------------------


def test_live_stats_reports_counts_participation_and_minority_bonus():
    """비율만으로는 "3명 중 2명(67%)"과 "200명 중 134명(67%)"이 구분되지
    않아 판단 근거가 못 된다. 인원수·참여 인원·소수파 배수까지 나와야 한다."""
    engine = PredictionEngine()
    engine.enroll_all({f"P{i}": "A팀" for i in range(10)})
    engine.open_round(1, ["A팀", "B팀", "C팀"])

    engine.set_target("P0", 1, "A팀")
    engine.set_target("P1", 1, "A팀")
    engine.set_target("P2", 1, "A팀")
    engine.set_target("P3", 1, "B팀")

    stats = engine.live_stats(1, candidates=["A팀", "B팀", "C팀"])

    assert stats["counts"] == {"A팀": 3, "B팀": 1, "C팀": 0}
    assert stats["chosen"] == 4
    assert stats["eligible"] == 10  # 명단 전원(고르지 않은 사람 포함)
    assert stats["distribution"]["A팀"] == pytest.approx(0.75)

    # 소수파 배수 = score_round의 1 + (1 - share)와 같은 식
    assert stats["minority_bonus"]["A팀"] == pytest.approx(1.25)
    assert stats["minority_bonus"]["B팀"] == pytest.approx(1.75)
    # 아무도 안 고른 후보가 2.0배로 가장 크고, 목록에서 사라지지 않아야 한다
    assert stats["minority_bonus"]["C팀"] == pytest.approx(2.0)


def test_live_stats_minority_bonus_matches_actual_scoring():
    """화면에 보여준 배수와 실제 채점 배수가 어긋나면 안 된다 -- 참가자가
    그 숫자를 보고 고르기 때문이다."""
    engine = PredictionEngine()
    engine.enroll_all({f"P{i}": "A팀" for i in range(4)})
    engine.open_round(1, ["A팀", "B팀"])
    engine.set_target("P0", 1, "A팀")
    engine.set_target("P1", 1, "A팀")
    engine.set_target("P2", 1, "A팀")
    engine.set_target("P3", 1, "B팀")

    shown = engine.live_stats(1, candidates=["A팀", "B팀"])["minority_bonus"]["B팀"]

    engine.lock_round(1, "seed")
    engine.score_round(1, ["B팀", "A팀"])  # B팀이 1위 -- 소수파 적중

    got = engine.cards["P3"].rewards[1]["predict"]
    expected = int(ROUND_BASE_POINTS * 1.0 * RANK_RATIOS[0] * shown)
    assert got == expected


def test_live_stats_excludes_auto_assigned_choices():
    """자동 배정은 "표"가 아니다 -- score_round의 분포 계산과 같은 규칙이라야
    창이 닫히는 순간의 화면 값과 실제 채점 분포가 일치한다."""
    engine = PredictionEngine()
    engine.enroll_all({f"P{i}": "A팀" for i in range(5)})
    engine.open_round(1, ["A팀", "B팀"])
    engine.set_target("P0", 1, "B팀")
    engine.lock_round(1, "seed")  # 나머지 4명은 자기 부서(A팀)로 자동 배정

    stats = engine.live_stats(1, candidates=["A팀", "B팀"])
    assert stats["chosen"] == 1
    assert stats["counts"] == {"A팀": 0, "B팀": 1}
