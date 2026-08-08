import pytest

from app import race
from app.fairness import compute_draw
from app.race import (
    LANE_COUNT,
    compute_effects,
    compute_tick,
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
    for hazard in layout1:
        assert 0.12 <= hazard["at_ratio"] <= 0.80
        assert 0 <= hazard["lane"] < LANE_COUNT
        assert hazard["penalty"] > 0


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


def test_position_at_with_seed_dips_then_recovers_exactly_to_target():
    """P000/round1/'obstacle-dip-seed'는 미리 확인해 둔, 실제로 장애물에
    맞는 조합이다. 맞는 순간부터 실제로(오프셋이 아니라 반환값 자체가)
    내려갔다가, progress_ratio=1.0에서는 항상 정확히 target과 일치해야
    한다(통과 판정과 어긋나면 안 된다)."""
    seed = "test-seed-1"
    pid = "P000"
    round_index = 1
    hits = kart_hits(seed, pid, round_index)
    assert hits, "이 테스트는 실제로 장애물에 맞는 조합을 전제로 한다"
    at_ratio = hits[0]["at_ratio"]

    total = 250
    rank_index = 10
    target = target_position(rank_index, total)

    just_after = position_at(rank_index, total, min(1.0, at_ratio + 0.01), pid, round_index, seed=seed)
    no_obstacle = position_at(rank_index, total, min(1.0, at_ratio + 0.01), pid, round_index, seed=None)

    assert just_after < no_obstacle, "장애물에 맞은 직후에는 실제로 위치가 내려가 있어야 한다"

    at_finish = position_at(rank_index, total, 1.0, pid, round_index, seed=seed)
    assert at_finish == pytest.approx(target), "결승선에서는 장애물과 무관하게 항상 목표 위치와 일치해야 한다"


def test_position_at_with_seed_never_goes_negative():
    for step in range(0, 101):
        ratio = step / 100
        pos = position_at(3, 40, ratio, "P003", round_index=2, seed="neg-check-seed")
        assert pos >= 0.0


def test_compute_effects_only_lists_karts_currently_hit():
    seed = "test-seed-1"
    ranking = [f"P{i:03d}" for i in range(30)]
    at_ratio = kart_hits(seed, "P000", 1)[0]["at_ratio"]

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
