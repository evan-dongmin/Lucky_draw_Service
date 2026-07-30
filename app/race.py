"""레이스 시뮬레이션: 고정 시간 진행 + 목표 순위로의 단순 보간 수렴.

기획안 §4.3(연출 스코프 축소)에 따라 정교한 러버밴딩 튜닝은 하지 않는다.
요구되는 것은 "결과 정합성(고정 시간 종료 시점에 정확히 목표 통과자/순위와
일치) + 어색하지 않은 움직임"뿐이다.

핵심 성질: 모든 카트의 위치는 (참가자 id, 라운드, 경과 비율)의 순수 함수다.
서버가 매 틱마다 다시 계산해 브로드캐스트하므로 클라이언트가 별도로
재현할 필요가 없다(모바일에는 이 데이터를 아예 보내지 않는다).
"""

from __future__ import annotations

import hashlib


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


def position_at(
    rank_index: int,
    total: int,
    progress_ratio: float,
    participant_id: str,
    round_index: int,
) -> float:
    """경과 비율(0..1)에서의 트랙 위치. progress_ratio=1.0에서 정확히
    target_position과 일치해 통과 판정이 항상 정확하다."""
    progress_ratio = min(max(progress_ratio, 0.0), 1.0)
    noise = _pseudo_noise(f"{participant_id}:{round_index}") - 0.5  # -0.5..0.5
    autonomous = 0.5 + noise * 0.9
    weight = progress_ratio**2  # 후반부로 갈수록 목표 쪽으로 급격히 수렴
    target = target_position(rank_index, total)
    return autonomous * (1 - weight) + target * weight


def compute_tick(ranking_ids: list[str], progress_ratio: float, round_index: int) -> dict[str, float]:
    total = len(ranking_ids)
    return {
        pid: position_at(idx, total, progress_ratio, pid, round_index)
        for idx, pid in enumerate(ranking_ids)
    }


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
