import pytest

from app.director import (
    MIN_SELECTION_WINDOW_SECONDS,
    MIN_TOTAL_SECONDS_WITH_PREDICTIONS,
    MIN_TOTAL_SECONDS_WITHOUT_PREDICTIONS,
    DirectorError,
    build_runbook,
    total_duration,
)


@pytest.mark.parametrize("total_seconds", [300, 240, 180, 600, 150])
def test_total_duration_matches_requested_seconds(total_seconds):
    segments = build_runbook(total_seconds=total_seconds, predictions_enabled=True)
    assert total_duration(segments) == pytest.approx(total_seconds, abs=0.01)


@pytest.mark.parametrize("total_seconds", [300, 240, 600, 150])
def test_selection_windows_never_below_minimum(total_seconds):
    segments = build_runbook(total_seconds=total_seconds, predictions_enabled=True)
    for seg in segments:
        if seg.is_selection_window:
            assert seg.duration_seconds >= MIN_SELECTION_WINDOW_SECONDS - 1e-6


def test_predictions_disabled_has_no_selection_windows():
    segments = build_runbook(total_seconds=300, predictions_enabled=False)
    assert all(not s.is_selection_window for s in segments)
    assert total_duration(segments) == pytest.approx(300, abs=0.01)


def test_predictions_disabled_gives_bonus_time_to_races():
    with_predictions = build_runbook(total_seconds=300, predictions_enabled=True)
    without_predictions = build_runbook(total_seconds=300, predictions_enabled=False)

    def race_total(segments):
        return sum(s.duration_seconds for s in segments if s.phase.startswith("race_"))

    assert race_total(without_predictions) > race_total(with_predictions)


def test_rejects_non_positive_total_seconds():
    with pytest.raises(DirectorError):
        build_runbook(total_seconds=0)
    with pytest.raises(DirectorError):
        build_runbook(total_seconds=-10)


def test_default_600_seconds_matches_runbook_proportions():
    segments = build_runbook()
    by_phase = {s.phase: s.duration_seconds for s in segments}
    # 기본 총 시간(600초)은 기준 비율표(합 264초)의 약 2.2727배로 스케일된다
    assert by_phase["opening"] == pytest.approx(600 / 264 * 10, abs=0.01)
    assert by_phase["race_r3"] == pytest.approx(600 / 264 * 30, abs=0.01)
    assert by_phase["score_r1_select_r2"] == pytest.approx(600 / 264 * 30, abs=0.01)
    # 결선(R3)은 카트가 적어 볼거리가 적으므로, 카트가 많은 R1/R2보다 짧아야 한다
    assert by_phase["race_r3"] < by_phase["race_r1"]
    assert by_phase["race_r3"] < by_phase["race_r2"]


def test_segment_order_is_stable():
    segments = build_runbook()
    phases = [s.phase for s in segments]
    assert phases == [
        "opening",
        "r1_lock",
        "race_r1",
        "score_r1_select_r2",
        "race_r2",
        "score_r2_select_r3",
        "race_r3",
        "final_announce",
        "verify",
    ]


def test_shortest_supported_time_still_enforces_selection_minimum_and_positive_races():
    # 지원 하한(150초)에서도 선택창 하한이 지켜지고 레이스 구간이 음수가 되지 않아야 한다
    segments = build_runbook(total_seconds=MIN_TOTAL_SECONDS_WITH_PREDICTIONS, predictions_enabled=True)
    for seg in segments:
        if seg.is_selection_window:
            assert seg.duration_seconds >= MIN_SELECTION_WINDOW_SECONDS - 1e-6
        assert seg.duration_seconds > 0
    assert total_duration(segments) == pytest.approx(MIN_TOTAL_SECONDS_WITH_PREDICTIONS, abs=0.01)


def test_total_seconds_below_floor_raises_with_predictions():
    with pytest.raises(DirectorError):
        build_runbook(total_seconds=MIN_TOTAL_SECONDS_WITH_PREDICTIONS - 1, predictions_enabled=True)


def test_total_seconds_below_floor_raises_without_predictions():
    with pytest.raises(DirectorError):
        build_runbook(total_seconds=MIN_TOTAL_SECONDS_WITHOUT_PREDICTIONS - 1, predictions_enabled=False)


def test_all_segment_durations_always_positive_across_range():
    for total_seconds in (150, 180, 200, 250, 300, 400, 600, 900):
        for predictions_enabled in (True, False):
            segments = build_runbook(total_seconds=total_seconds, predictions_enabled=predictions_enabled)
            for seg in segments:
                assert seg.duration_seconds > 0, (total_seconds, predictions_enabled, seg)
