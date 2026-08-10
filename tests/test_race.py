import pytest

from app import race
from app.fairness import compute_draw
from app.race import (
    LANE_COUNT,
    compute_effects,
    compute_tick,
    crossing_ratio,
    department_live_rates,
    kart_hits,
    lane_for,
    obstacle_layout,
    pass_line,
    position_at,
    target_position,
    total_obstacle_penalty,
)
from app.roster import generate_sample_participants


def test_target_position_is_monotonically_decreasing_with_rank():
    total = 50
    positions = [target_position(i, total) for i in range(total)]
    assert positions == sorted(positions, reverse=True)
    assert positions[0] == pytest.approx(1.0)


def test_pass_line_sits_between_last_passer_and_first_non_passer():
    total, pass_count = 100, 40
    last_passer = target_position(pass_count - 1, total)
    first_non_passer = target_position(pass_count, total)
    line = pass_line(pass_count, total)
    assert first_non_passer < line < last_passer


def test_pass_line_extreme_all_pass_or_none_pass():
    assert pass_line(0, 100) > target_position(0, 100)
    assert pass_line(100, 100) < target_position(99, 100)


@pytest.mark.parametrize("rank_index,total", [(0, 250), (99, 250), (249, 250), (0, 1)])
def test_position_at_full_progress_matches_target_exactly(rank_index, total):
    pos = position_at(rank_index, total, progress_ratio=1.0, participant_id="P001", round_index=1)
    assert pos == pytest.approx(target_position(rank_index, total))


def test_all_karts_start_at_the_starting_line():
    """모든 카트는 출발선(0)에서 함께 출발해야 한다. 예전 구현은 트랙 전역에
    흩뿌린 상태로 시작해 '함께 출발해 전진한다'는 감각이 없었다."""
    for rank in (0, 40, 249):
        pos = position_at(rank, 250, progress_ratio=0.0, participant_id=f"P{rank}", round_index=1)
        assert pos == pytest.approx(0.0)


@pytest.mark.parametrize("rank_index", [0, 17, 120, 249])
def test_position_is_monotonically_increasing_never_moves_backward(rank_index):
    """진행률이 늘면 위치도 반드시 늘어야 한다(뒤로 밀리면 레이스가 깨져 보인다)."""
    prev = -1.0
    for step in range(0, 101):
        pos = position_at(rank_index, 250, step / 100, f"P{rank_index}", round_index=1)
        assert pos >= prev - 1e-12, f"위치가 뒤로 감: step={step}"
        prev = pos


def test_mid_race_order_differs_from_final_order_so_overtaking_happens():
    """페이스 지수 편차 때문에 레이스 중간 순위와 최종 순위가 달라야 한다
    (달라야 추월 장면이 생긴다)."""
    ids = [f"P{i:03d}" for i in range(40)]
    mid = sorted(ids, key=lambda p: -position_at(ids.index(p), 40, 0.35, p, 1))
    final = sorted(ids, key=lambda p: -position_at(ids.index(p), 40, 1.0, p, 1))
    assert mid != final, "중간 순위가 최종 순위와 같으면 추월이 전혀 없다"


def test_compute_tick_is_pure_function_of_inputs():
    ranking = [f"P{i:03d}" for i in range(20)]
    tick1 = compute_tick(ranking, progress_ratio=0.5, round_index=1)
    tick2 = compute_tick(ranking, progress_ratio=0.5, round_index=1)
    assert tick1 == tick2


def _population_for_round(draw, round_index: int) -> list[str]:
    if round_index == 1:
        return draw.ranking
    return draw.round_pass_ids[round_index - 1]


@pytest.mark.parametrize("trial", range(20))
@pytest.mark.parametrize("participant_count,draw_count", [(250, 3), (250, 8), (60, 5), (12, 2)])
def test_full_progress_partition_matches_fairness_pass_sets(trial, participant_count, draw_count):
    """race.py의 통과선 판정이 fairness.py의 round_pass_ids와 100% 일치해야 한다
    (DoD: 100회 자동 실행에서 통과자가 Fairness 결과와 100% 일치)."""
    participants = generate_sample_participants(participant_count, seed=1)
    draw = compute_draw(
        "s1", participants, draw_count=draw_count, seed=f"race-trial-{trial}-{participant_count}-{draw_count}"
    )

    for round_index in (1, 2, 3):
        population = _population_for_round(draw, round_index)
        pass_ids = set(draw.round_pass_ids[round_index])
        total = len(population)
        count = len(pass_ids)

        tick = compute_tick(population, progress_ratio=1.0, round_index=round_index)
        line = pass_line(count, total)

        for idx, pid in enumerate(population):
            is_passer = pid in pass_ids
            if is_passer:
                assert tick[pid] >= line, (round_index, idx, pid, tick[pid], line)
            else:
                assert tick[pid] < line, (round_index, idx, pid, tick[pid], line)


def test_department_live_rates_matches_fairness_at_full_progress():
    participants = generate_sample_participants(250, seed=7)
    draw = compute_draw("s1", participants, draw_count=5, seed="dept-race-seed")

    departments = draw.snapshot["departments"]
    r1_pass_set = set(draw.round_pass_ids[1])

    # round 1: 분모는 부서 전체 인원 (fairness.py와 동일한 규칙)
    r1_denom = {name: set(ids) for name, ids in departments.items()}
    r1_positions = compute_tick(draw.ranking, progress_ratio=1.0, round_index=1)
    r1_line = pass_line(len(draw.round_pass_ids[1]), len(draw.ranking))
    live_rates_r1 = department_live_rates(r1_positions, r1_denom, r1_line)

    for name, rate in live_rates_r1.items():
        assert rate == pytest.approx(draw.department_pass_rate[1][name])

    # round 2: 분모는 부서 ∩ R1 통과자 (fairness.py와 동일한 규칙)
    r2_denom = {name: set(ids) & r1_pass_set for name, ids in departments.items()}
    r2_population = draw.round_pass_ids[1]
    r2_positions = compute_tick(r2_population, progress_ratio=1.0, round_index=2)
    r2_line = pass_line(len(draw.round_pass_ids[2]), len(r2_population))
    live_rates_r2 = department_live_rates(r2_positions, r2_denom, r2_line)

    for name, rate in live_rates_r2.items():
        assert rate == pytest.approx(draw.department_pass_rate[2][name])


# ---------------------------------------------------------------------------
# 장애물 (작업계획서 §12-4)
# ---------------------------------------------------------------------------


def test_lane_for_is_deterministic_and_in_range():
    for round_index in (1, 2, 3):
        lane1 = lane_for("seed-a", "P001", round_index)
        lane2 = lane_for("seed-a", "P001", round_index)
        assert lane1 == lane2
        assert 0 <= lane1 < LANE_COUNT


def test_lane_for_differs_by_seed_or_round_typically():
    lanes = {lane_for(f"seed-{i}", "P001", 1) for i in range(30)}
    assert len(lanes) > 1, "시드가 달라져도 항상 같은 레인이면 결정론이 아니라 상수다"


def test_obstacle_layout_is_deterministic_and_well_formed():
    layout1 = obstacle_layout("seed-x", round_index=1)
    layout2 = obstacle_layout("seed-x", round_index=1)
    assert layout1 == layout2
    assert len(layout1) == race.HAZARDS_PER_ROUND
    lo, hi = race.HAZARD_SPAN
    for hazard in layout1:
        assert lo <= hazard["at_fraction"] <= hi
        assert 0 <= hazard["lane"] < LANE_COUNT
        assert hazard["penalty"] > 0
        # 화면에서 움직이게 하는 파라미터 -- 좌우 흔들림이 자기 레인을
        # 벗어나면 "내 레인이 아닌데 왜 맞았지"로 보인다.
        assert 0 < hazard["drift_amp"] <= race.HAZARD_DRIFT_LANES
        assert hazard["drift_speed"] > 0


def test_every_obstacle_type_has_a_distinct_look_and_motion_profile():
    """사용자 요청: "장애물이 각각 특색이 있도록. 움직임과 패턴도 다
    제각각이고, 크기도 카트보다 큰 것 등도 있게끔."

    예전에는 8종이 전부 같은 사인파에 진폭만 난수로 달라 화면에서는 이모지만
    다른 같은 물체로 보였다. 카탈로그의 모든 종류가 프로필을 갖는지, 그리고
    그 프로필이 실제로 서로 구별되는지(움직임 패턴이 한 종류로 쏠리지 않고,
    크기 차이가 눈에 띄는 폭인지)를 고정한다."""
    catalog_types = {t for t, _ in race.OBSTACLE_CATALOG}
    # 카탈로그에 있는데 프로필이 없으면 KeyError로 레이스가 죽는다
    assert catalog_types == set(race.OBSTACLE_PROFILES)

    motions = {p["motion"] for p in race.OBSTACLE_PROFILES.values()}
    assert len(motions) >= 5, "움직임 패턴이 몇 종류로 쏠려 있습니다"

    sizes = [p["size"] for p in race.OBSTACLE_PROFILES.values()]
    # 기준 크기가 이미 카트 높이의 1.05배라, 1.5를 넘으면 확실히 카트보다 크다
    assert max(sizes) >= 1.5, "카트보다 확실히 큰 장애물이 없습니다"
    assert min(sizes) <= 0.85, "작고 앙증맞은 장애물이 없습니다"

    # **무거운 것일수록 덜 움직인다** -- 큰 장애물이 옆 레인까지 흔들려 나가면
    # "저건 내 레인이 아닌데 왜 안 맞았지"로 보인다(판정은 레인 일치로만 한다).
    biggest = max(race.OBSTACLE_PROFILES.values(), key=lambda p: p["size"])
    smallest = min(race.OBSTACLE_PROFILES.values(), key=lambda p: p["size"])
    assert biggest["drift"] < smallest["drift"]


def test_obstacle_render_params_follow_their_type_profile():
    """개체별 편차가 있어도 **종류의 성격은 유지**돼야 한다 -- 같은 종류의
    콘 두 개가 완전히 똑같이 흔들려도 안 되지만, 바위가 콘만 해져도 안 된다."""
    by_type: dict[str, list[dict]] = {}
    for round_index in (1, 2, 3):
        for hazard in obstacle_layout("profile-seed", round_index, 0.7):
            by_type.setdefault(hazard["type"], []).append(hazard)

    for obstacle_type, hazards in by_type.items():
        profile = race.OBSTACLE_PROFILES[obstacle_type]
        for hazard in hazards:
            assert hazard["motion"] == profile["motion"]
            assert hazard["squash"] == profile["squash"]
            # 크기 편차는 프로필 기준 ±12% 안
            assert profile["size"] * 0.87 <= hazard["size_scale"] <= profile["size"] * 1.13
            lo, hi = profile["speed"]
            assert lo <= hazard["drift_speed"] <= hi
            spin_lo, spin_hi = profile["spin"]
            assert spin_lo <= hazard["spin_speed"] <= spin_hi


def test_obstacles_are_placed_before_the_finish_line_with_even_spacing():
    """사용자 요청: 장애물은 결승선 **이후**가 아니라 결승선까지 가는
    도중에 적절한 간격으로 놓여야 한다."""
    pass_line_value = 0.55  # R1처럼 결승선이 트랙 중간쯤인 경우
    layout = obstacle_layout("spacing-seed", 1, pass_line_value)

    # (1) 전부 결승선 앞쪽에 있다
    assert all(0 < h["at_ratio"] < pass_line_value for h in layout)

    # (2) 한곳에 뭉치지 않는다 -- 이웃 간격이 균등 슬롯의 절반 이상
    positions = sorted(h["at_ratio"] for h in layout)
    gaps = [b - a for a, b in zip(positions, positions[1:])]
    lo, hi = race.HAZARD_SPAN
    even_gap = (hi - lo) * pass_line_value / race.HAZARDS_PER_ROUND
    assert min(gaps) > even_gap * 0.5, f"장애물이 뭉쳐 있습니다: {gaps}"

    # (3) 결승선이 더 앞이면 장애물도 함께 앞으로 당겨진다(비율 배치)
    tight = obstacle_layout("spacing-seed", 1, 0.3)
    assert all(h["at_ratio"] < 0.3 for h in tight)


def test_obstacle_placement_does_not_change_penalty_totals():
    """결승선 위치는 "어디서 맞는지"만 바꾸고 "얼마나 맞는지"는 못 바꾼다.

    이게 깨지면 순위 -> 통과선 -> 장애물 -> 페널티 -> 순위로 순환 참조가
    생겨 결정론이 무너진다(app/race.py의 _hazard_specs 설명 참고)."""
    seed = "no-circular-seed"
    for pid in (f"P{i:03d}" for i in range(40)):
        base = total_obstacle_penalty(seed, pid)
        # obstacle_layout을 어떤 결승선으로 부르든 페널티 합은 그대로다
        obstacle_layout(seed, 1, 0.3)
        obstacle_layout(seed, 1, 0.9)
        assert total_obstacle_penalty(seed, pid) == base


def test_obstacle_layout_differs_by_round_for_same_seed():
    r1 = obstacle_layout("seed-y", 1)
    r2 = obstacle_layout("seed-y", 2)
    assert r1 != r2


def test_kart_hits_only_includes_matching_lane():
    seed = "hit-seed"
    pid = "P042"
    lane = lane_for(seed, pid, 1)
    hits = kart_hits(seed, pid, 1)
    assert all(h["lane"] == lane for h in hits)
    assert set(h["id"] for h in hits) == {
        h["id"] for h in obstacle_layout(seed, 1) if h["lane"] == lane
    }


def test_total_obstacle_penalty_is_deterministic_nonnegative_and_capped():
    for pid in (f"P{i:03d}" for i in range(60)):
        penalty = total_obstacle_penalty("cap-seed", pid)
        assert penalty == total_obstacle_penalty("cap-seed", pid)
        assert 0.0 <= penalty <= race.OBSTACLE_PENALTY_CAP


def test_total_obstacle_penalty_applies_regardless_of_rank():
    """사용자 요청: "중간 순위뿐 아니라 전체 카트에 모두 적용되도록" --
    장애물 페널티 계산 자체는 순위(rank_index)를 아예 인자로 받지 않으므로
    선두든 꼴찌든(다른 이유로 순위가 갈릴 뿐) 동일한 규칙이 적용된다."""
    import inspect

    assert "rank_index" not in inspect.signature(total_obstacle_penalty).parameters


def test_position_at_without_seed_is_unaffected_by_obstacles():
    """seed를 안 넘기면(기존 테스트 다수가 이 경로) 장애물 영향이 전혀 없다."""
    pos = position_at(0, 250, 0.5, "P000", round_index=1)
    pos_explicit_none = position_at(0, 250, 0.5, "P000", round_index=1, seed=None)
    assert pos == pos_explicit_none


def test_position_at_slows_the_kart_down_while_an_obstacle_is_in_effect():
    """장애물에 맞으면 **진행 속도가 실제로 떨어져야** 한다(사용자 요청:
    "맞으면 실질적 패널티가 없어 보여. 속도가 느려진다던가, 잠시 멈춘다던가").

    예전 구현은 목표 위치에서 penalty(최대 0.03)만큼 빼고 남은 구간 전체에
    걸쳐 되돌려주는 방식이라 순간 속도 변화가 사실상 0이었다 -- 화면에서는
    아무 일도 일어나지 않는 것처럼 보였다. 이제는 맞은 구간 동안 속도 자체가
    떨어진다. 여기서는 그 감속이 **눈에 띄는 크기인지**를 고정한다."""
    seed = "test-seed-1"
    pid = "P000"
    round_index = 1
    hits = kart_hits(seed, pid, round_index)
    assert hits, "이 테스트는 실제로 장애물에 맞는 조합을 전제로 한다"

    total, rank_index = 250, 10
    hit = min(hits, key=lambda h: h["at_fraction"])
    at_ratio = hit["at_fraction"]  # pass_line_value 미지정 = 트랙 전체 기준
    duration = race.OBSTACLE_IMPACT[hit["type"]]["duration"]

    def speed_around(ratio: float) -> float:
        step = 0.004
        lo = position_at(rank_index, total, ratio, pid, round_index, seed=seed)
        hi = position_at(rank_index, total, ratio + step, pid, round_index, seed=seed)
        return (hi - lo) / step

    before = speed_around(max(0.0, at_ratio - 0.02))
    during = speed_around(at_ratio + duration * 0.3)

    assert during < before * 0.75, (
        f"장애물에 맞았는데 속도가 거의 안 줄었습니다: {before:.3f} -> {during:.3f}"
    )


def test_position_at_recovers_exactly_to_target_at_the_finish():
    """감속을 넣어도 progress_ratio=1.0에서는 항상 정확히 target과 일치해야
    한다. 통과 판정이 전적으로 이 성질에 의존한다(어긋나면 화면에서 넘은
    카트가 탈락하는 사고가 난다)."""
    seed = "test-seed-1"
    pid = "P000"
    round_index = 1
    assert kart_hits(seed, pid, round_index), "장애물에 맞는 조합을 전제로 한다"

    total, rank_index = 250, 10
    at_finish = position_at(rank_index, total, 1.0, pid, round_index, seed=seed)
    assert at_finish == pytest.approx(target_position(rank_index, total))


def test_obstacle_effect_is_only_active_during_its_impact_window():
    """연출 효과는 **감속이 실제로 걸린 구간에서만** 켜져야 한다.

    예전에는 맞은 지점부터 결승선까지 강도가 서서히 줄어서, 트랙 20%에서
    맞은 카트가 90% 지점에서도 여전히 "맞는 중"으로 표시됐다 -- 늘 켜져
    있으니 아무 의미가 없었다."""
    seed = "test-seed-1"
    pid = "P000"
    round_index = 1
    hits = kart_hits(seed, pid, round_index)
    assert hits
    hit = min(hits, key=lambda h: h["at_fraction"])
    at_ratio = hit["at_fraction"]
    duration = race.OBSTACLE_IMPACT[hit["type"]]["duration"]

    # 맞기 직전: 아무 효과 없음
    assert race.active_effect_at(seed, pid, round_index, max(0.0, at_ratio - 0.01)) is None
    # 맞은 직후: 해당 종류의 효과가 최대 강도에 가깝게 걸린다
    just_after = race.active_effect_at(seed, pid, round_index, at_ratio + duration * 0.05)
    assert just_after is not None and just_after["type"] == hit["type"]
    assert just_after["strength"] > 0.8
    # 구간이 끝난 뒤에는 꺼진다
    assert race.active_effect_at(seed, pid, round_index, at_ratio + duration * 1.5) is None


def test_position_at_with_seed_never_goes_negative():
    for step in range(0, 101):
        ratio = step / 100
        pos = position_at(3, 40, ratio, "P003", round_index=2, seed="neg-check-seed")
        assert pos >= 0.0


def test_compute_effects_only_lists_karts_currently_hit():
    seed = "test-seed-1"
    ranking = [f"P{i:03d}" for i in range(30)]
    at_ratio = kart_hits(seed, "P000", 1)[0]["at_fraction"]

    before = compute_effects(ranking, max(0.0, at_ratio - 0.05), round_index=1, seed=seed)
    after = compute_effects(ranking, min(1.0, at_ratio + 0.01), round_index=1, seed=seed)

    assert "P000" not in before
    assert "P000" in after
    assert after["P000"]["type"]
    assert 0 < after["P000"]["strength"] <= 1.0

    at_finish = compute_effects(ranking, 1.0, round_index=1, seed=seed)
    assert at_finish == {}, "결승선에서는 모든 효과가 회복되어 있어야 한다"


def test_obstacle_penalty_actually_changes_final_ranking_from_pure_hmac_order():
    """장애물이 실제 순위에 영향을 줘야 한다(작업계획서 §12-4) --
    여러 시드를 시도해 최소 한 번은 장애물 반영 순위가 순수 HMAC 순위와
    달라야 한다(항상 같으면 장애물이 장식일 뿐 결과에 영향이 없다는 뜻)."""
    from app.fairness import _score

    participants = generate_sample_participants(120, seed=3)
    ids = [p.id for p in participants]

    found_difference = False
    for i in range(20):
        seed = f"ranking-impact-seed-{i}"
        draw = compute_draw("s1", participants, draw_count=3, seed=seed)
        pure_hmac_ranking = sorted(ids, key=lambda pid: (-_score(seed, pid), pid))
        if draw.ranking != pure_hmac_ranking:
            found_difference = True
            break

    assert found_difference, "20번 시도했는데도 장애물이 순위를 한 번도 못 바꿨다"


# ---------------------------------------------------------------------------
# 결승선 컷오프 (작업계획서 §12-8)
# ---------------------------------------------------------------------------


def test_crossing_ratio_matches_target_reaching_the_line():
    """rank_index=0(1등, target=1.0)은 pass_line이 낮을수록 더 일찍 통과해야 한다."""
    early = crossing_ratio(0, 250, 0.2, "P000", round_index=1, seed="cross-seed")
    late = crossing_ratio(0, 250, 0.9, "P000", round_index=1, seed="cross-seed")
    assert early is not None and late is not None
    assert early < late


def test_crossing_ratio_is_none_when_target_never_reaches_the_line():
    """target이 pass_line보다 낮은 카트는 끝까지 못 넘는다(None)."""
    # rank_index=249(꼴찌, total=250)의 target은 0.2 -- pass_line 0.5는 못 넘는다.
    ratio = crossing_ratio(249, 250, 0.5, "P249", round_index=1, seed="cross-seed")
    assert ratio is None


def test_crossing_ratio_is_deterministic():
    a = crossing_ratio(10, 250, 0.6, "P010", round_index=1, seed="cross-seed-2")
    b = crossing_ratio(10, 250, 0.6, "P010", round_index=1, seed="cross-seed-2")
    assert a == b


def test_obstacle_layout_cache_returns_immutable_tuple():
    layout = obstacle_layout("cache-seed", 1)
    assert isinstance(layout, tuple)
    assert obstacle_layout("cache-seed", 1) == layout
    # 캐시는 위치 무관 명세(_hazard_specs)에 걸려 있다 -- 결승선마다 절대
    # 위치가 달라지므로 obstacle_layout 자체는 매번 새 튜플을 만든다.
    # 튜플이라 호출부가 캐시된 내부 값을 실수로 변형할 수 없다.
    assert race._hazard_specs("cache-seed", 1) is race._hazard_specs("cache-seed", 1)
