"""Director Agent: 10분 시간 예산의 총괄 관리자.

기획안 §4의 런북(비율 10+10+42+30+42+30+30+35+35, 기준 합 264초)을 총
시간에 맞춰 비례 스케일링한다(기본 총 시간은 600초 -- 기준 합의 약
2.27배로 스케일된다). 예측 게임이 꺼져 있으면 선택창 구간을 짧은 발표로
줄이고 남는 시간을 레이스 구간에 재배분한다.

선택창(선택 후보 대상을 고르는 구간)은 최소 30초를 보장한다 -- 자동
규칙 같은 도피처가 없으므로, 창이 짧으면 다수가 무작위 배정으로 밀려
예측 게임의 목적 자체가 무너지기 때문이다(기획안 §4 참조). 이 하한
보정으로 늘어난 시간은 레이스 구간에서 비례 축소해 총합을 맞춘다.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_TOTAL_SECONDS = 600.0
MIN_SELECTION_WINDOW_SECONDS = 30.0

# 선택창 2개(각 30초 하한)만으로도 이 값을 넘으므로, 레이스 구간이 음수가
# 되는 것을 막기 위한 최소 총 시간. 실제 서비스는 항상 600초 근방을 쓰므로
# 이 하한은 비정상적으로 짧은 입력을 거부하는 안전장치일 뿐이다.
MIN_TOTAL_SECONDS_WITH_PREDICTIONS = 150.0
MIN_TOTAL_SECONDS_WITHOUT_PREDICTIONS = 60.0

# (구간명, 기준 초, 선택창 포함 여부) -- 기준 총합은 264초
#
# race_r3(결선)는 원래 70초로 가장 길었는데, 결선은 카트가 5~10대뿐이라
# 볼거리가 적은데도 총 600초 기준 140초나 끌어서 "너무 느리다"는 피드백을
# 받았다(사용자 요청). 40초로 줄이고 회수한 30초를 카트가 많아 볼거리가
# 실제로 많은 R1/R2와 발표 구간에 재배분한다.
#
# 이후 "1라운드 카트 속도가 너무 빠르다"는 피드백(사용자 요청)을 받아
# race_r1/r2/r3를 추가로 줄였다(55->42, 55->42, 40->30). 기본 총 시간
# 600초 기준 라운드당 체감 진행 시간이 약 13~15% 짧아진다(회수된 시간은
# 아래 스케일링에 의해 나머지 구간에 고르게 재배분됨). 카트가 화면을
# 가로지르는 속도 자체는 `race.py`의 `pace_exponent` 하한 조정으로 함께
# 고쳤다 -- 라운드 길이만 줄이면 진행률(ratio)이 실시간 대비 더 빨리
# 흘러 오히려 체감 속도가 올라가므로, 두 수정이 함께 있어야 의도한
# "부담 없이 짧고 자연스러운 속도"가 된다.
_BASE_SEGMENTS: tuple[tuple[str, float, bool], ...] = (
    ("opening", 10.0, False),
    ("r1_lock", 10.0, False),
    ("race_r1", 42.0, False),
    ("score_r1_select_r2", 30.0, True),
    ("race_r2", 42.0, False),
    ("score_r2_select_r3", 30.0, True),
    ("race_r3", 30.0, False),
    ("final_announce", 35.0, False),
    ("verify", 35.0, False),
)

_RACE_PHASES = {"race_r1", "race_r2", "race_r3"}
_NON_SELECTION_ANNOUNCE_SECONDS = 10.0  # 예측 게임 꺼졌을 때 선택창을 대체할 발표 시간


class DirectorError(ValueError):
    pass


@dataclass
class RunbookSegment:
    phase: str
    duration_seconds: float
    is_selection_window: bool

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "duration_seconds": round(self.duration_seconds, 3),
            "is_selection_window": self.is_selection_window,
        }


def _without_predictions(
    segments: tuple[tuple[str, float, bool], ...],
) -> list[tuple[str, float, bool]]:
    reclaimed = 0.0
    adjusted: list[tuple[str, float, bool]] = []
    for name, duration, is_selection in segments:
        if is_selection:
            reclaimed += duration - _NON_SELECTION_ANNOUNCE_SECONDS
            adjusted.append((name, _NON_SELECTION_ANNOUNCE_SECONDS, False))
        else:
            adjusted.append((name, duration, is_selection))

    race_count = sum(1 for name, _, _ in adjusted if name in _RACE_PHASES)
    bonus = reclaimed / race_count if race_count else 0.0
    return [
        (name, duration + bonus if name in _RACE_PHASES else duration, is_selection)
        for name, duration, is_selection in adjusted
    ]


def build_runbook(
    total_seconds: float = DEFAULT_TOTAL_SECONDS,
    predictions_enabled: bool = True,
) -> list[RunbookSegment]:
    if total_seconds <= 0:
        raise DirectorError("총 시간은 0보다 커야 합니다")
    floor = (
        MIN_TOTAL_SECONDS_WITH_PREDICTIONS
        if predictions_enabled
        else MIN_TOTAL_SECONDS_WITHOUT_PREDICTIONS
    )
    if total_seconds < floor:
        raise DirectorError(f"총 시간이 너무 짧습니다 (최소 {floor:.0f}초 필요)")

    segments = list(_BASE_SEGMENTS) if predictions_enabled else _without_predictions(_BASE_SEGMENTS)

    base_total = sum(d for _, d, _ in segments)
    scale = total_seconds / base_total

    result = [
        RunbookSegment(phase=name, duration_seconds=duration * scale, is_selection_window=is_sel)
        for name, duration, is_sel in segments
    ]

    # 선택창 하한 보정
    for seg in result:
        if seg.is_selection_window and seg.duration_seconds < MIN_SELECTION_WINDOW_SECONDS:
            seg.duration_seconds = MIN_SELECTION_WINDOW_SECONDS

    # 하한 보정으로 늘어난 만큼 레이스 구간에서 비례 축소해 총합을 맞춘다
    total_after = sum(s.duration_seconds for s in result)
    diff = total_after - total_seconds
    if abs(diff) > 1e-9:
        race_segments = [s for s in result if s.phase in _RACE_PHASES]
        race_total = sum(s.duration_seconds for s in race_segments)
        if race_total > 0:
            for seg in race_segments:
                seg.duration_seconds -= diff * (seg.duration_seconds / race_total)

    return result


def total_duration(segments: list[RunbookSegment]) -> float:
    return sum(s.duration_seconds for s in segments)
