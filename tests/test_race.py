import pytest

from app.fairness import compute_draw
from app.race import compute_tick, department_live_rates, pass_line, position_at, target_position
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


def test_position_at_progress_zero_ignores_target_uses_autonomous_noise():
    pos_a = position_at(0, 250, progress_ratio=0.0, participant_id="A", round_index=1)
    pos_b = position_at(249, 250, progress_ratio=0.0, participant_id="B", round_index=1)
    # 시작 시점엔 순위와 무관하게 노이즈 기반이라 값 범위가 목표값과 다를 수 있다
    assert 0.0 <= pos_a <= 1.0
    assert 0.0 <= pos_b <= 1.0


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
