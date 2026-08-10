"""레이스 시뮬레이션: 고정 시간 진행 + 목표 순위로의 단순 보간 수렴.

기획안 §4.3(연출 스코프 축소)에 따라 정교한 러버밴딩 튜닝은 하지 않는다.
요구되는 것은 "결과 정합성(고정 시간 종료 시점에 정확히 목표 통과자/순위와
일치) + 어색하지 않은 움직임"뿐이다.

핵심 성질: 모든 카트의 위치는 (참가자 id, 라운드, 경과 비율)의 순수 함수다.
서버가 매 틱마다 다시 계산해 브로드캐스트하므로 클라이언트가 별도로
재현할 필요가 없다(모바일에는 이 데이터를 아예 보내지 않는다).

**장애물(작업계획서 §12-4, 2026-08-08)**: 장애물은 더 이상 순수 연출이
아니다. `obstacle_layout`/`lane_for`가 커밋 시드로부터 라운드별 장애물
배치와 카트별 고정 레인을 결정론적으로 만들고, 그 결과(`total_obstacle_penalty`)가
`fairness.py`의 최종 순위 계산에 실제로 반영된다(누가 이기는지가 바뀐다).
동시에 `position_at`에 `seed`를 넘기면 그 장애물에 맞는 순간 트랙 위치가
실제로(연출용 오프셋이 아니라 서버가 계산해 내려주는 진짜 값으로) 잠깐
내려갔다가 회복한다 -- 단, `progress_ratio=1.0`에서는 항상 정확히
`target_position`과 일치하도록 설계해(회복량이 (1-at_ratio) 구간에 걸쳐
선형으로 정확히 0에 수렴) 통과 판정과는 절대 어긋나지 않는다.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any


def _pseudo_noise(seed_material: str) -> float:
    """0..1 사이 결정론적 의사난수. 연출용일 뿐 공정성 계산과 무관하다."""
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def target_position(rank_index: int, total: int) -> float:
    """순위(0=1등)를 0.2~1.0 트랙 진행률로 매핑한다."""
    if total <= 1:
        return 1.0
    return 1.0 - (rank_index / (total - 1)) * 0.8


def pass_line(pass_count: int, total: int) -> float:
    """통과선 위치: 마지막 통과자와 첫 탈락자 목표 위치의 중간값."""
    if total <= 0:
        return 0.5
    if pass_count <= 0:
        return 1.01  # 아무도 통과하지 못하는 극단값(트랙 끝보다 위)
    if pass_count >= total:
        return -0.01  # 전원 통과(트랙 시작보다 아래)
    last_passer = target_position(pass_count - 1, total)
    first_non_passer = target_position(pass_count, total)
    return (last_passer + first_non_passer) / 2


def pace_exponent(participant_id: str, round_index: int) -> float:
    """카트별 페이스 성향. 1보다 작으면 초반에 치고 나가고, 크면 후반에
    몰아친다. 이 편차 때문에 중간 순위와 최종 순위가 달라져 추월이
    자연스럽게 발생한다(결과 자체는 바뀌지 않는다).

    하한을 0.8로 둔다(예전 0.55) -- position = target * ratio**exponent는
    exponent<1이면 ratio→0 근처에서 기울기가 매우 가팔라(0.55일 때 ratio
    5%만 지나도 target의 21%까지 도달), 특히 카메라가 따라가는 R1 선두가
    이 값에 걸리면 출발하자마자 화면을 가로질러 순간이동하듯 보였다
    (사용자 피드백: "1라운드에서 카트 속도가 너무 빠르다"). 0.8~1.7로
    좁혀도 추월 편차(페이스 다양성)는 충분히 남는다."""
    return 0.8 + _pseudo_noise(f"{participant_id}:{round_index}:pace") * 0.9


# ---------------------------------------------------------------------------
# 장애물 (작업계획서 §12-4) -- 시드 결정론 + 실제 순위/위치 반영
#
# 화면 크기·참가자 수와 무관하게 고정된 레인 수를 쓴다(장애물 판정이
# 클라이언트 렌더링 상태에 좌우되면 결정론이 깨진다). "누가 몇 번 맞는지"는
# 순전히 (시드, 참가자 id, 라운드) 해시로만 정해지고, 트랙 진행률 도달
# 여부와는 무관하게 레인만 일치하면 맞는다 -- 사용자 요청("중간 순위뿐
# 아니라 전체 카트에 모두 적용되도록")대로 선두든 꼴찌든 동일 확률로
# 장애물 페널티를 받는다(도달 여부로 걸러내면 선두권만 유리해진다).
# ---------------------------------------------------------------------------

LANE_COUNT = 8
HAZARDS_PER_ROUND = 10

# (종류, 페널티) -- 페널티는 target_position과 같은 0..1 스케일. 맞으면
# 이만큼 최종 순위 산정(및 실시간 트랙 위치)에서 깎인다.
OBSTACLE_CATALOG: tuple[tuple[str, float], ...] = (
    ("cone", 0.006),
    ("oil", 0.012),
    ("tire", 0.010),
    ("banana", 0.018),
    ("puddle", 0.014),
    ("rock", 0.022),
    ("bomb", 0.030),
    ("ice", 0.016),
)

# 한 카트가 3라운드 통틀어 받을 수 있는 장애물 페널티 총합의 상한. 레인 해시가
# 우연히 겹쳐 한 카트가 그 라운드 장애물을 거의 다 맞는 극단적인 경우에도
# HMAC 기본 순위 신호가 완전히 묻히지 않도록 안전장치를 둔다.
OBSTACLE_PENALTY_CAP = 0.4


def lane_for(seed: str, participant_id: str, round_index: int) -> int:
    """이 카트가 이 라운드에 배정되는 고정 레인(0..LANE_COUNT-1).

    화면 크기·참가자 수와 무관하게 (시드, 참가자, 라운드)만으로 정해지므로
    같은 커밋에서는 항상 같은 레인이다(서버·클라이언트가 각자 계산해도
    항상 일치)."""
    return int(_pseudo_noise(f"{seed}:{participant_id}:{round_index}:lane") * LANE_COUNT)


# 장애물이 놓이는 구간 -- **결승선(통과선)까지 가는 도중**의 몇 %~몇 %인지
# (사용자 요청: "결승선 이후가 아니라 결승선까지 가는 도중에 적절한 간격으로").
# 절대 진행률이 아니라 결승선까지의 **비율**이라, 라운드마다 결승선 위치가
# 달라도 장애물은 항상 결승선 앞쪽에만 고르게 깔린다.
HAZARD_SPAN = (0.08, 0.92)

# 장애물이 자기 슬롯 안에서 흔들릴 수 있는 최대 폭(슬롯 길이 대비). 1.0이면
# 옆 장애물과 겹칠 수 있으므로 0.7로 둔다 -- "적절한 간격"을 유지하면서도
# 기계적으로 정확히 등간격은 아니게 만드는 값.
HAZARD_JITTER = 0.7

# 장애물이 좌우로 흔들리는 최대 폭(레인 너비 대비). 0.5를 넘으면 옆 레인을
# 침범해 "저 장애물은 내 레인이 아닌데 왜 맞았지"처럼 보이므로, 레인 정체성이
# 유지되는 선에서만 움직이게 한다(충돌 판정은 레인 일치로만 정해진다).
HAZARD_DRIFT_LANES = 0.38

# 장애물 종류별 "성격"(사용자 요청: "장애물이 각각 특색이 있도록. 움직임과
# 패턴도 다 제각각이고, 크기도 카트보다 큰 것 등도 있게끔").
#
# 예전에는 8종이 **전부 같은 사인파 좌우 흔들림**에 진폭·주기만 난수로
# 달랐고 크기도 0.85~1.35배 안에서만 흔들려서, 화면에서는 사실상 이모지만
# 다른 같은 물체로 보였다. 이제 종류마다 움직임 패턴 자체가 다르다.
#
# **설계 규칙: 무겁고 큰 것일수록 덜 움직이고, 가볍고 작은 것일수록 많이
# 돌아다닌다.** 물리적으로 납득되기도 하지만, 실용적인 이유가 더 크다 --
# 충돌 판정은 서버가 **레인 일치로만** 하므로, 큰 장애물이 옆 레인까지
# 흔들려 나가면 "저건 내 레인이 아닌데 왜 안 맞았지"로 보인다. 그래서 큰
# 것에는 거의 0에 가까운 drift를 준다.
#
# 값 의미:
#   motion  : 클라이언트가 고를 움직임 패턴(static/jitter/roll/tumble/
#             pulse/glide/seep -- static.js의 obstacleScreenPoints 참고)
#   size    : 기본 장애물 크기 배수. 기준 크기 자체가 카트 높이의 1.05배라
#             1.0을 넘으면 이미 카트보다 크고, rock(1.95)은 약 2배다.
#   drift   : 좌우 흔들림 폭(레인 너비 대비, HAZARD_DRIFT_LANES 이하)
#   speed   : 흔들림 주기(Hz) 범위
#   spin    : 회전 속도(회/초) 범위. 타이어는 빠르게 구르고 바위는 거의 안 돈다
#   squash  : 가로(레인 방향) 대 세로(진행 방향) 비. 1보다 작으면 레인
#             방향으로 납작해져 **옆 레인을 안 건드리면서도** 커 보인다 --
#             기름/웅덩이처럼 바닥에 퍼진 것에 쓴다
OBSTACLE_PROFILES: dict[str, dict[str, Any]] = {
    # 작고 가벼운 것 -- 자주, 넓게 돌아다닌다
    "cone": {"motion": "jitter", "size": 0.80, "drift": 0.30,
             "speed": (0.9, 1.7), "spin": (-0.35, 0.35), "squash": 1.0},
    "banana": {"motion": "tumble", "size": 0.75, "drift": 0.26,
               "speed": (0.9, 1.5), "spin": (-2.8, 2.8), "squash": 1.0},
    # 구르는 것 -- 좌우로 왕복하면서 진행 방향으로 빠르게 회전
    "tire": {"motion": "roll", "size": 1.10, "drift": 0.34,
             "speed": (0.45, 0.8), "spin": (2.2, 3.4), "squash": 1.0},
    # 터질 듯 맥동하는 것 -- 자리는 거의 안 옮기고 크기가 뛴다
    "bomb": {"motion": "pulse", "size": 1.30, "drift": 0.12,
             "speed": (1.4, 2.2), "spin": (-0.9, 0.9), "squash": 1.0},
    # 미끄러지는 것 -- 느리고 넓게 활강한다
    "ice": {"motion": "glide", "size": 1.55, "drift": 0.20,
            "speed": (0.18, 0.36), "spin": (-0.3, 0.3), "squash": 0.95},
    # 바닥에 퍼진 것 -- 거의 안 움직이고, 레인 방향으로 살짝 납작하다.
    # squash를 0.6까지 내려봤더니 이모지 자체가 찌그러져 무슨 물건인지
    # 알아볼 수 없었다(기름통이 파란 마름모로 보였다). 레인 침범을 막는
    # 효과는 유지하면서 형태는 알아볼 수 있는 선인 0.75~0.85로 잡는다.
    "oil": {"motion": "seep", "size": 1.65, "drift": 0.05,
            "speed": (0.14, 0.30), "spin": (-0.05, 0.05), "squash": 0.85},
    "puddle": {"motion": "seep", "size": 1.75, "drift": 0.04,
               "speed": (0.12, 0.26), "spin": (-0.04, 0.04), "squash": 0.75},
    # 가장 크고 무거운 것 -- 사실상 고정. 카트 2배 크기의 위압감이 목적이다
    "rock": {"motion": "static", "size": 1.95, "drift": 0.02,
             "speed": (0.08, 0.18), "spin": (-0.1, 0.1), "squash": 1.0},
}


@lru_cache(maxsize=64)
def _hazard_specs(seed: str, round_index: int) -> tuple[dict[str, Any], ...]:
    """이 라운드 장애물의 **위치 무관** 명세(레인·종류·페널티 + 배치 비율).

    여기서 `at_fraction`은 절대 진행률이 아니라 **결승선까지의 비율**(0..1)
    이다. 절대 위치는 결승선 값이 필요하므로 `obstacle_layout`에서 곱한다.

    이렇게 나눠 둔 이유가 중요하다: 순위 계산에 쓰이는 페널티 합
    (`total_obstacle_penalty`)은 **레인 일치로만** 정해져서 결승선 위치를
    전혀 필요로 하지 않는다. 만약 이 함수가 결승선을 인자로 받으면
    "순위 -> 통과자 수 -> 결승선 -> 장애물 -> 페널티 -> 순위"로 순환 참조가
    생긴다. 위치가 필요한 것은 dip 타이밍·화면 렌더뿐이고, 그쪽은 결승선이
    이미 확정된 뒤에만 호출된다(R1 결승선 -> R1 통과자 -> R2 결승선 -> ...
    순방향 체인이라 순환이 없다).

    `lru_cache`를 건다 -- 결승선 컷오프의 크로싱 타임 계산(`crossing_ratio`)이
    카트마다 수백 번씩 반복 호출하는데, 매번 해시를 다시 계산하면 250명
    규모에서 눈에 띄게 느려진다. (시드, 라운드) 조합은 커밋 하나당 최대
    3개뿐이라 캐시 크기 걱정은 없다. 튜플로 반환해 캐시된 내부 객체를
    호출부가 실수로 변형할 수 없게 막는다."""
    lo, hi = HAZARD_SPAN
    slot = (hi - lo) / HAZARDS_PER_ROUND
    hazards: list[dict[str, Any]] = []
    for i in range(HAZARDS_PER_ROUND):
        base = f"{seed}:{round_index}:hazard:{i}"
        # 균등 슬롯의 중앙을 기준으로 슬롯 안에서만 흔든다 -- 예전처럼 구간
        # 전체에 균일 난수를 뿌리면 몇 개가 한곳에 뭉치고 넓은 구간이 텅 빈다.
        center = lo + slot * (i + 0.5)
        jitter = (_pseudo_noise(f"{base}:pos") - 0.5) * slot * HAZARD_JITTER
        at_fraction = min(hi, max(lo, center + jitter))
        lane = int(_pseudo_noise(f"{base}:lane") * LANE_COUNT)
        type_idx = int(_pseudo_noise(f"{base}:type") * len(OBSTACLE_CATALOG))
        obstacle_type, penalty = OBSTACLE_CATALOG[type_idx]
        profile = OBSTACLE_PROFILES[obstacle_type]

        def pick(key: str, lo_hi: tuple[float, float]) -> float:
            """종류별 범위 안에서 시드로 하나 고른다. 같은 종류라도 개체마다
            조금씩 다르게(같은 콘 두 개가 똑같이 흔들리지 않게) 만들되,
            범위 자체가 종류마다 달라 성격은 유지된다."""
            low, high = lo_hi
            return low + _pseudo_noise(f"{base}:{key}") * (high - low)

        hazards.append(
            {
                "id": f"r{round_index}-{i}",
                "at_fraction": at_fraction,
                "lane": lane,
                "type": obstacle_type,
                "penalty": penalty,
                # 화면에서 살아 움직이게 하는 파라미터(연출 전용, 전부 시드
                # 파생이라 관전자 화면 여러 대가 똑같이 움직인다).
                # 종류별 성격(OBSTACLE_PROFILES)에 개체별 편차를 얹는다.
                "motion": profile["motion"],
                "squash": profile["squash"],
                # 개체 편차를 **곱한 뒤에** 상한을 건다 -- 먼저 자르면 편차가
                # 상한을 다시 넘겨버린다(레인 이탈 회귀 테스트가 잡아준 실수).
                "drift_amp": min(
                    HAZARD_DRIFT_LANES,
                    profile["drift"] * (0.75 + _pseudo_noise(f"{base}:amp") * 0.5),
                ),
                "drift_speed": pick("speed", profile["speed"]),  # Hz
                "drift_phase": _pseudo_noise(f"{base}:phase"),  # 0..1 (x2pi)
                "spin_speed": pick("spin", profile["spin"]),  # 회전/초
                # 개체별 크기 편차는 ±12%만 -- 이보다 크게 흔들면 "바위인데
                # 콘만 하다" 같은 게 나와 종류별 크기 차이가 흐려진다.
                "size_scale": profile["size"] * (0.88 + _pseudo_noise(f"{base}:size") * 0.24),
            }
        )
    return tuple(hazards)


def has_finish_line(pass_line_value: float | None) -> bool:
    """이 라운드에 **의미 있는 결승선이 있는가**.

    `pass_line`은 전원 통과 시 -0.01, 전원 탈락 시 1.01 같은 축퇴값을
    돌려준다. 판정용으로는 올바르지만(전원 통과면 모든 위치가 -0.01보다
    크다) "화면에 선을 어디 그릴지"나 "1등이 언제 선을 넘는지"의 기준으로
    쓰면 곧바로 망가진다 -- 출발하자마자 전원이 통과한 것으로 잡히기
    때문이다. 그런 라운드에는 컷오프·카운트다운을 아예 걸지 않는다.

    전원 통과는 드문 경우가 아니다: 참가자가 100명 이하이면 R1 통과 정원
    (R1_PASS_COUNT=100)이 전체 인원보다 커서 항상 이 상태가 된다.
    """
    return pass_line_value is not None and 0 < pass_line_value <= 1.0


def hazard_line(pass_line_value: float | None) -> float:
    """장애물 배치의 기준이 되는 결승선 위치.

    의미 있는 결승선이 없으면(전원 통과/전원 탈락, 또는 값이 없는 테스트
    호출) **트랙 전체**를 쓴다. 예전에는 0.25로 하한을 뒀는데, 전원 통과
    (-0.01)일 때 그 하한에 걸려 장애물 10개가 전부 트랙 앞 25%에 뭉치고
    나머지 75%가 텅 비었다(참가자 100명 이하 행사에서 항상 발생)."""
    if not has_finish_line(pass_line_value):
        return 1.0
    return pass_line_value  # type: ignore[return-value]


def obstacle_layout(
    seed: str, round_index: int, pass_line_value: float | None = None
) -> tuple[dict[str, Any], ...]:
    """이 라운드의 장애물 배치(절대 트랙 진행률 x 레인 + 움직임 파라미터).

    `at_ratio`는 **결승선 앞쪽 구간에만** 놓이도록 결승선 값을 곱해 만든다.
    (시드, 라운드, 결승선)만으로 정해지며 참가자 개개인·화면 크기와는
    무관하다 -- 커밋 시점에 이미 확정된다."""
    line = hazard_line(pass_line_value)
    return tuple({**h, "at_ratio": h["at_fraction"] * line} for h in _hazard_specs(seed, round_index))


def kart_hits(seed: str, participant_id: str, round_index: int) -> list[dict[str, Any]]:
    """이 카트가 이 라운드에 실제로 맞는 장애물 목록(레인 일치 기준).

    결승선 위치와 무관하다 -- 어디에 놓이든 "레인이 같으면 맞는다"이므로
    페널티 합(=순위)이 결승선에 의존하지 않는다(`_hazard_specs` 설명 참고)."""
    lane = lane_for(seed, participant_id, round_index)
    return [h for h in _hazard_specs(seed, round_index) if h["lane"] == lane]


def round_obstacle_penalty(seed: str, participant_id: str, round_index: int) -> float:
    """이 카트가 이 라운드에서 받는 장애물 페널티 합(0..1 스케일, target_position과 동일 단위)."""
    return sum(h["penalty"] for h in kart_hits(seed, participant_id, round_index))


def total_obstacle_penalty(seed: str, participant_id: str) -> float:
    """3라운드 전체를 통틀어 이 카트가 받는 장애물 페널티 합.
    `fairness.py`의 최종 순위 조정에 쓰인다(순환 참조 없이 한 번에 계산됨:
    레인·장애물 배치 모두 순위와 무관하게 시드에서만 파생되기 때문)."""
    total = sum(round_obstacle_penalty(seed, participant_id, r) for r in (1, 2, 3))
    return min(total, OBSTACLE_PENALTY_CAP)


# 장애물에 맞았을 때 **카트가 실제로 어떻게 느려지는가**(2026-08-10, 사용자
# 요청: "장애물에 카트가 맞으면 실질적 패널티가 없어 보여. 속도가 느려진다던가,
# 잠시 멈춘다던가. 장애물 특성에 맞게 패널티를 적용해 줘").
#
# 예전에는 맞은 순간 목표 위치에서 `penalty`(0.006~0.030)만큼 빼고 결승선까지
# 선형으로 되돌리는 방식이었다. 트랙 전체가 1.0인데 최대 3%를 **전체 남은
# 구간에 걸쳐** 나눠 돌려주니 순간 속도 변화가 사실상 0이라, 화면에서는
# 아무 일도 안 일어나는 것처럼 보였다.
#
# 이제 위치를 빼는 대신 **속도를 떨어뜨린다**. 맞은 지점부터 duration 동안
# 진행 속도가 speed 배로 줄고(0에 가까우면 사실상 정지), 그만큼 뒤처진
# 거리는 남은 구간에서 저절로 따라잡는다(아래 _speed_profile의 정규화).
#
#   speed    : 감속 구간 동안의 진행 속도 배수(1.0 = 평소, 0.05 = 거의 정지)
#   duration : 감속이 지속되는 구간 길이(트랙 진행률 기준)
#
# 종류별 성격은 OBSTACLE_VISUAL(stage.js)의 kind와 맞춘다 -- 화면에서 스핀이
# 걸리는 장애물은 실제로도 크게 멈추고, 미끄러지는 장애물은 오래 완만하게
# 느려진다. "무거운 장애물일수록 크게 멈춘다"가 기본 감각이다.
OBSTACLE_IMPACT: dict[str, dict[str, float]] = {
    "cone": {"speed": 0.72, "duration": 0.035},  # 살짝 휘청
    "tire": {"speed": 0.52, "duration": 0.060},  # 눈에 띄게 감속
    "oil": {"speed": 0.45, "duration": 0.075},  # 오래 미끄러지며 감속
    "puddle": {"speed": 0.48, "duration": 0.070},
    "ice": {"speed": 0.40, "duration": 0.085},  # 가장 오래 끌린다
    "banana": {"speed": 0.16, "duration": 0.055},  # 팽그르르 -- 거의 정지
    "rock": {"speed": 0.10, "duration": 0.050},  # 쿵 -- 정지에 가까움
    "bomb": {"speed": 0.06, "duration": 0.065},  # 완전 정지
}

# 감속 구간이 아닌 곳의 기본 속도.
_NORMAL_SPEED = 1.0


def _hit_progress(at_ratio: float, target: float, exponent: float) -> float | None:
    """이 카트가 트랙 위치 `at_ratio`(장애물이 놓인 자리)에 **실제로 닿는**
    실효 진행률 u. 닿지 못하면 None.

    카트 위치는 `target * u**exponent`이므로, 위치가 at_ratio가 되는 u는
    `(at_ratio / target) ** (1/exponent)`다. 목표 위치보다 뒤에 놓인 장애물은
    이 카트가 영영 도달하지 못하므로(예: 목표 0.2인 후미 카트에게 0.6에 놓인
    장애물) 감속을 걸지 않는다 -- 닿지도 않은 자리에서 느려지면 화면과 판정이
    어긋나 보인다. 순위 페널티는 이와 무관하게 레인 일치로만 정해지므로
    (OBSTACLE_CATALOG) 최종 결과에는 영향이 없다."""
    if target <= 0:
        return None
    fraction = at_ratio / target
    if fraction >= 1.0:
        return None
    if fraction <= 0.0:
        return 0.0
    return fraction ** (1.0 / exponent)


@lru_cache(maxsize=8192)
def _speed_segments(
    seed: str, participant_id: str, round_index: int, line: float, target: float
) -> tuple[tuple[float, float, float], ...]:
    """감속 구간표: `(u_시작, u_끝, 그 지점까지 걸리는 누적 시간)`.

    **u는 진행률(progress_ratio)이 아니라 "실효 진행률"이다.** 카트 위치가
    `target * u**pace`이므로, 구간을 u로 잡아야 "장애물에 닿는 순간"과
    "감속이 시작되는 순간"이 정확히 일치한다.

    예전에는 구간을 progress_ratio 공간에 잡았는데, 카트마다 pace가 달라
    진행률과 위치가 어긋난다. 실측: 목표 0.807 / pace 1.26인 카트는 트랙
    위치 0.608의 장애물에 progress_ratio 0.804에서 닿는데 감속은 0.608부터
    걸렸다 -- 95초 라운드에서 약 19초 어긋난 셈이라, 화면에서는 장애물을
    그냥 지나치거나 엉뚱한 곳에서 느려지는 것으로 보였다(사용자 피드백:
    "장애물 부딪힌 후 효과가 좀 늦게 나타나네").

    누적 시간은 `du / speed`의 적분이다(느린 구간일수록 같은 u를 지나는 데
    시간이 더 걸린다). 마지막 원소의 누적값으로 나눠 정규화하므로 **뒤처진
    만큼은 남은 구간에서 반드시 따라잡고, progress_ratio=1.0에서는 u=1.0이
    되어 정확히 목표 위치에 도달한다**(공정성 판정이 여기에 의존한다).

    (시드, 참가자, 라운드, 결승선, 목표위치)만으로 정해지는 정적인 값이라
    캐시한다 -- crossing_ratio가 카트당 수백 번 position_at을 부른다.
    """
    exponent = pace_exponent(participant_id, round_index)
    windows: list[tuple[float, float, float]] = []
    for h in kart_hits(seed, participant_id, round_index):
        impact = OBSTACLE_IMPACT.get(h["type"])
        if impact is None:
            continue
        start = _hit_progress(h["at_fraction"] * line, target, exponent)
        if start is None:
            continue
        end = min(1.0, start + impact["duration"])
        if end > start:
            windows.append((start, end, impact["speed"]))

    if not windows:
        return ((0.0, 1.0, 1.0),)

    # 모든 구간 경계를 모아 잘게 쪼갠 뒤, 각 조각의 속도를 결정한다.
    # 구간이 겹치면 더 느린 쪽을 쓴다(연달아 맞으면 더 심하게 막힌다).
    edges = sorted({0.0, 1.0, *(w[0] for w in windows), *(w[1] for w in windows)})
    segments: list[tuple[float, float, float]] = []
    cumulative = 0.0
    for lo, hi in zip(edges, edges[1:]):
        if hi <= lo:
            continue
        mid = (lo + hi) / 2
        speed = _NORMAL_SPEED
        for w_start, w_end, w_speed in windows:
            if w_start <= mid < w_end:
                speed = min(speed, w_speed)
        cumulative += (hi - lo) / speed
        segments.append((lo, hi, cumulative))
    return tuple(segments)


def _effective_progress(
    progress_ratio: float, segments: tuple[tuple[float, float, float], ...]
) -> float:
    """진행률(경과 시간 비율) -> 실효 진행률 u. 단조 증가하고 1.0에서 정확히 1.0.

    `_speed_segments`가 만든 "u -> 누적 시간" 표를 거꾸로 읽는다. 감속 구간
    에서는 시간이 흘러도 u가 조금밖에 안 늘어나(=느려지거나 멈춘 것처럼 보이고),
    그 대신 나머지 구간에서 u가 빠르게 늘어 따라잡는다."""
    total = segments[-1][2]
    if total <= 0:
        return progress_ratio
    if progress_ratio <= 0.0:
        return 0.0
    if progress_ratio >= 1.0:
        return 1.0
    elapsed = progress_ratio * total
    previous = 0.0
    for lo, hi, cumulative in segments:
        if elapsed <= cumulative:
            span_time = cumulative - previous
            if span_time <= 0:
                return hi
            return lo + (elapsed - previous) / span_time * (hi - lo)
        previous = cumulative
    return 1.0


def active_effect_at(
    seed: str,
    participant_id: str,
    round_index: int,
    progress_ratio: float,
    pass_line_value: float | None = None,
    target: float | None = None,
) -> dict[str, Any] | None:
    """이 순간 이 카트에 걸려 있는 장애물 효과(연출용 -- 스핀/사운드 트리거에
    쓰라고 강도까지 함께 내려준다). 없으면 None.

    **감속이 실제로 걸려 있는 구간에서만** 효과를 돌려준다. 예전에는 맞은
    지점부터 결승선까지 강도가 서서히 줄도록 해서, 트랙 20%에서 맞은 카트가
    90% 지점에서도 여전히 "맞는 중"으로 표시됐다 -- 늘 켜져 있으니 아무
    의미가 없었다.

    `target`(이 카트의 최종 목표 위치)을 넘기면 `_speed_segments`와 **완전히
    같은 u-공간 구간**을 쓰므로, 화면 연출(스핀·사운드)과 실제 감속이 정확히
    같은 순간에 시작된다. 안 넘기면 감속 없이 진행률만으로 계산하던 예전
    방식으로 폴백한다(테스트 등 target을 모르는 호출부용)."""
    line = hazard_line(pass_line_value)
    exponent = pace_exponent(participant_id, round_index)
    if target is not None:
        effective = _effective_progress(
            progress_ratio,
            _speed_segments(seed, participant_id, round_index, line, target),
        )
    else:
        effective = progress_ratio
    best: dict[str, Any] | None = None
    for h in kart_hits(seed, participant_id, round_index):
        impact = OBSTACLE_IMPACT.get(h["type"])
        if impact is None:
            continue
        if target is None:
            start = h["at_fraction"] * line
        else:
            hit = _hit_progress(h["at_fraction"] * line, target, exponent)
            if hit is None:
                continue  # 이 카트는 저 장애물까지 도달하지 못한다
            start = hit
        duration = impact["duration"]
        # 구간 끝은 `_speed_segments`와 **똑같이** 1.0에서 자른다. 안 자르면
        # 결승선 직전에 맞은 카트가 progress_ratio=1.0에서도 여전히 "맞는 중"
        # 으로 남아, 이미 최종 위치에 선 카트가 화면에서 계속 스핀한다.
        end = min(1.0, start + duration)
        if effective < start or effective >= end:
            continue
        strength = 1.0 - (effective - start) / duration
        if strength <= 0:
            continue
        if best is None or strength > best["strength"]:
            best = {"type": h["type"], "strength": strength, "id": h["id"]}
    return best


def position_at(
    rank_index: int,
    total: int,
    progress_ratio: float,
    participant_id: str,
    round_index: int,
    seed: str | None = None,
    pass_line_value: float | None = None,
) -> float:
    """경과 비율(0..1)에서의 트랙 위치.

    설계 원칙:
    - 모든 카트는 출발선(0)에서 함께 출발한다.
    - progress_ratio=1.0에서 정확히 target_position과 일치해 통과 판정이
      항상 정확하다(공정성 판정은 여기에만 의존한다).
    - 카트마다 페이스 지수가 달라 레이스 중간에는 순위가 뒤섞이고,
      끝에서 목표 순위로 수렴한다.
    - `seed`를 넘기면(실제 레이스 진행 중에는 항상 넘긴다) 그 라운드의
      장애물에 맞는 시점부터 실제로 위치가 내려갔다가 결승선까지 선형
      회복한다(§12-4). `seed`가 없으면(주로 테스트) 예전과 동일하게 장애물
      영향이 전혀 없는 순수 곡선을 반환한다 -- 통과 판정 자체는 이미
      `fairness.py`가 장애물까지 반영해 확정한 순위(`rank_index`)로 결정돼
      있으므로 어느 쪽이든 ratio=1.0 값은 같다.

    (이전 구현은 카트를 트랙 전역에 흩뿌린 상태에서 목표로 수렴시켰기 때문에
     일부 카트가 뒤로 밀려 보였고 "함께 출발해 전진한다"는 감각이 없었다.)
    """
    progress_ratio = min(max(progress_ratio, 0.0), 1.0)
    target = target_position(rank_index, total)
    if seed is None:
        return target * (progress_ratio ** pace_exponent(participant_id, round_index))
    # 장애물에 맞으면 그 구간 동안 **진행 속도**가 떨어진다(위치를 빼는 게
    # 아니다). 실효 진행률로 바꿔 넣으므로 위치는 절대 뒤로 가지 않고,
    # 감속 구간에서는 눈에 띄게 느려지거나 멈춘 것처럼 보이며, 뒤처진 만큼은
    # 남은 구간에서 따라잡는다. _warp(1.0)은 항상 정확히 1.0이라
    # progress_ratio=1.0에서 target_position과 어긋날 수 없다.
    effective = _effective_progress(
        progress_ratio,
        _speed_segments(seed, participant_id, round_index, hazard_line(pass_line_value), target),
    )
    return target * (effective ** pace_exponent(participant_id, round_index))


def crossing_ratio(
    rank_index: int,
    total: int,
    pass_line_value: float,
    participant_id: str,
    round_index: int,
    seed: str,
    steps: int = 500,
) -> float | None:
    """이 카트가 결승선(`pass_line_value`)에 처음 도달하는 progress_ratio.

    장애물 dip 때문에 곡선이 국소적으로 흔들릴 수 있어(맞는 순간 잠깐
    내려갔다가 회복) 이분탐색 대신 선형 스캔한다 -- `steps=500`이면
    ~95초짜리 라운드에서도 0.2초 미만 오차라 결승선 컷오프(§12-8, 5~10초
    단위)에 충분한 정밀도다. 결승선까지 못 미치면(순위상 애초에 통과권이
    아닌 경우) None."""
    if pass_line_value <= 0:
        return 0.0
    for i in range(1, steps + 1):
        ratio = i / steps
        position = position_at(
            rank_index,
            total,
            ratio,
            participant_id,
            round_index,
            seed=seed,
            pass_line_value=pass_line_value,
        )
        if position >= pass_line_value:
            return ratio
    return None


def compute_tick(
    ranking_ids: list[str],
    progress_ratio: float,
    round_index: int,
    seed: str | None = None,
    pass_line_value: float | None = None,
) -> dict[str, float]:
    total = len(ranking_ids)
    return {
        pid: position_at(
            idx, total, progress_ratio, pid, round_index, seed=seed, pass_line_value=pass_line_value
        )
        for idx, pid in enumerate(ranking_ids)
    }


def compute_effects(
    ranking_ids: list[str],
    progress_ratio: float,
    round_index: int,
    seed: str,
    pass_line_value: float | None = None,
) -> dict[str, dict[str, Any]]:
    """이 틱에서 장애물 효과가 걸려 있는 카트만 담은 맵(pid -> {type, strength}).
    무대 화면이 스핀/사운드를 언제 재생할지는 이 값을 그대로 쓴다 --
    클라이언트가 충돌 판정을 직접 재현할 필요가 없다."""
    effects: dict[str, dict[str, Any]] = {}
    total = len(ranking_ids)
    for idx, pid in enumerate(ranking_ids):
        # 목표 위치를 함께 넘겨야 감속 구간과 연출 구간이 정확히 같은 순간에
        # 시작된다(안 넘기면 진행률 기준 폴백이라 카트마다 어긋난다).
        effect = active_effect_at(
            seed,
            pid,
            round_index,
            progress_ratio,
            pass_line_value,
            target=target_position(idx, total),
        )
        if effect is not None:
            effects[pid] = {"type": effect["type"], "strength": round(effect["strength"], 3)}
    return effects


def camera_anchor(ranking_ids: list[str], progress_ratio: float, round_index: int) -> float:
    """무대 카메라가 따라갈 **장애물 감속을 뺀** 선두 위치.

    카메라가 실제 선두 카트를 따라가면, 선두가 폭탄에 맞아 멈추는 순간
    카메라도 같이 멈춰 **화면 전체가 얼어붙는다** -- 관객에게는 맞지도 않은
    나머지 카트까지 함께 느려진 것처럼 보인다(사용자 피드백: "장애물에
    충돌한 카트 말고 다른 카트들도 그 영향을 받는 거 같은데").

    장애물이 순수 연출이던 시절에는 서버 위치에 감속이 없어서 이 문제가
    없었지만, §12-13에서 감속을 실제 위치에 반영하면서 드러났다. 그래서
    카메라에는 감속을 뺀 자연스러운 곡선을 따로 내려준다 -- 카메라는 일정하게
    흐르고, 맞은 카트만 그 자리에서 뒤로 처져 무리에 삼켜지는 것이 보인다."""
    total = len(ranking_ids)
    if total == 0:
        return 0.0
    return max(
        position_at(idx, total, progress_ratio, pid, round_index)
        for idx, pid in enumerate(ranking_ids)
    )


def department_live_rates(
    positions: dict[str, float],
    denom_sets: dict[str, set[str]],
    threshold: float,
) -> dict[str, float]:
    """부서별 '현재 통과선 위에 있는 비율' -- 실시간 랭킹 표시용.
    progress_ratio=1.0일 때 fairness.py의 최종 department_pass_rate와 일치한다."""
    rates: dict[str, float] = {}
    for name, ids in denom_sets.items():
        if not ids:
            rates[name] = 0.0
            continue
        above = sum(1 for pid in ids if positions.get(pid, 0.0) >= threshold)
        rates[name] = above / len(ids)
    return rates
