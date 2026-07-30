import pytest

from app.fairness import (
    FairnessError,
    compute_commit,
    compute_draw,
    recompute_from_reveal,
    resolve_finalist_count,
    reveal,
    verify_draw,
)
from app.models import Participant
from app.roster import generate_sample_participants


def _participants(n: int = 250):
    return generate_sample_participants(count=n, seed=1)


@pytest.mark.parametrize(
    "n,expected",
    [(1, 5), (3, 6), (5, 10), (8, 10), (10, 10), (20, 20)],
)
def test_resolve_finalist_count_guarantees_n_le_f(n, expected):
    assert resolve_finalist_count(n) == expected
    assert resolve_finalist_count(n) >= n


def test_resolve_finalist_count_rejects_zero():
    with pytest.raises(FairnessError):
        resolve_finalist_count(0)


def test_determinism_same_seed_same_result():
    participants = _participants(120)
    draw1 = compute_draw("s1", participants, draw_count=3, seed="fixed-seed", created_at="2026-07-30T00:00:00Z")
    draw2 = compute_draw("s1", participants, draw_count=3, seed="fixed-seed", created_at="2026-07-30T00:00:00Z")
    assert draw1.winners == draw2.winners
    assert draw1.ranking == draw2.ranking
    assert draw1.commit == draw2.commit


def test_different_seed_different_ranking_likely():
    participants = _participants(120)
    draw1 = compute_draw("s1", participants, draw_count=3, seed="seed-a")
    draw2 = compute_draw("s1", participants, draw_count=3, seed="seed-b")
    assert draw1.ranking != draw2.ranking


@pytest.mark.parametrize("n", [1, 2, 5, 8, 15])
def test_nested_survivor_sets_hold_for_any_n(n):
    participants = _participants(250)
    draw = compute_draw("s1", participants, draw_count=n, seed=f"seed-{n}")

    winners = set(draw.winners)
    r2_pass = set(draw.round_pass_ids[2])
    r1_pass = set(draw.round_pass_ids[1])

    assert winners <= r2_pass
    assert r2_pass <= r1_pass
    assert len(draw.round_pass_ids[2]) == draw.finalist_count
    assert draw.finalist_count >= n


def test_nested_survivor_sets_hold_for_small_population():
    participants = _participants(12)
    draw = compute_draw("s1", participants, draw_count=2, seed="small-seed")
    winners = set(draw.winners)
    r2_pass = set(draw.round_pass_ids[2])
    r1_pass = set(draw.round_pass_ids[1])
    assert winners <= r2_pass <= r1_pass
    assert r1_pass <= set(p.id for p in participants)


def test_excluded_participants_never_appear_anywhere():
    participants = _participants(50)
    excluded = [p.id for p in participants[:10]]
    draw = compute_draw("s1", participants, draw_count=3, excluded_ids=excluded, seed="excl-seed")

    touched = set(draw.ranking) | set(draw.winners)
    for round_ids in draw.round_pass_ids.values():
        touched |= set(round_ids)

    assert touched.isdisjoint(excluded)


def test_draw_count_larger_than_eligible_raises():
    participants = _participants(5)
    with pytest.raises(FairnessError):
        compute_draw("s1", participants, draw_count=10, seed="seed")


def test_department_pass_rate_no_whole_department_elimination():
    """부서 통과율이 부분적(0<rate<1)일 수 있어야 한다 -- 즉 생존이
    부서 단위가 아니라 개별 카트 단위로 결정된다는 증거."""
    participants = _participants(250)
    draw = compute_draw("s1", participants, draw_count=5, seed="dept-seed")

    rates_r1 = draw.department_pass_rate[1]
    assert len(rates_r1) >= 1
    # 최소 하나의 부서는 0과 1 사이의 통과율을 가져야 한다(부서 전원 통과/전원 탈락이 아님)
    partial = [r for r in rates_r1.values() if 0 < r < 1]
    assert partial, f"부서 통과율이 전부 0 또는 1입니다: {rates_r1}"


def test_department_pass_rate_bounds():
    participants = _participants(250)
    draw = compute_draw("s1", participants, draw_count=5, seed="bounds-seed")
    for round_rates in draw.department_pass_rate.values():
        for rate in round_rates.values():
            assert 0.0 <= rate <= 1.0


def test_commit_changes_when_snapshot_changes():
    participants = _participants(50)
    draw1 = compute_draw("s1", participants, draw_count=2, seed="same-seed")

    tampered = list(participants)
    tampered[0] = Participant(id=tampered[0].id, name="바뀐이름", team=tampered[0].team)
    draw2 = compute_draw("s1", tampered, draw_count=2, seed="same-seed")

    assert draw1.commit != draw2.commit


def test_commit_changes_when_draw_count_changes():
    participants = _participants(50)
    draw1 = compute_draw("s1", participants, draw_count=2, seed="same-seed")
    draw2 = compute_draw("s1", participants, draw_count=3, seed="same-seed")
    assert draw1.commit != draw2.commit


def test_reveal_and_verify_round_trip():
    participants = _participants(80)
    draw = compute_draw("s1", participants, draw_count=3, seed="verify-seed")
    reveal(draw)
    assert draw.revealed is True
    assert draw.revealed_at is not None
    assert verify_draw(draw) is True


def test_verify_detects_tampered_winners():
    participants = _participants(80)
    draw = compute_draw("s1", participants, draw_count=3, seed="verify-seed-2")
    reveal(draw)
    draw.winners = list(reversed(draw.winners)) if len(draw.winners) > 1 else ["fake-id"]
    assert verify_draw(draw) is False


def test_recompute_from_reveal_matches_original_commit():
    participants = _participants(60)
    draw = compute_draw("s1", participants, draw_count=2, seed="recompute-seed")
    recomputed = recompute_from_reveal(draw.seed, draw.snapshot)
    assert recomputed["commit"] == draw.commit
    assert recomputed["winners"] == draw.winners
    assert recomputed["ranking"] == draw.ranking


def test_canonical_json_is_deterministic_regardless_of_key_order():
    a = {"b": 1, "a": {"z": 1, "y": 2}}
    b = {"a": {"y": 2, "z": 1}, "b": 1}
    assert compute_commit("seed", a) == compute_commit("seed", b)
