"""Fairness Agent: 커밋-리빌 추첨 + 부서 대항 중첩 생존 집합.

핵심 불변식(모든 draw_count·부서 분포에서 반드시 성립):
    winners(N) ⊆ round_pass_ids[2](F) ⊆ round_pass_ids[1](R1 통과)

부서 통과율은 표시·예측용 파생 지표일 뿐, 생존 여부를 결정하지 않는다.
생존은 항상 HMAC 순위의 개별 상위 절단으로만 결정된다(부서 통째 탈락 금지).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.departments import compute_department_groups
from app.models import DrawResult, Participant

R1_PASS_COUNT = 100


class FairnessError(ValueError):
    pass


def canonical_json(data: Any) -> str:
    """Python과 verify.js 양쪽에서 동일한 바이트열이 나오도록 하는 정규화 직렬화.

    - 키를 재귀적으로 정렬한다 (json.dumps sort_keys=True는 중첩 dict에도 적용됨).
    - 구분자에 공백을 넣지 않는다.
    - ensure_ascii=False로 한글을 이스케이프하지 않는다 (JS도 기본이 이와 동일).
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_commit(seed: str, snapshot: dict[str, Any]) -> str:
    payload = f"{seed}|{canonical_json(snapshot)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _score(seed: str, participant_id: str) -> int:
    digest = hmac.new(seed.encode("utf-8"), participant_id.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest, "big")


def resolve_finalist_count(draw_count: int) -> int:
    """F(결선 진출 카트 수). N <= F가 항상 성립하도록 보장한다."""
    if draw_count < 1:
        raise FairnessError("당첨 인원수는 1 이상이어야 합니다")
    clamped = max(5, min(10, draw_count * 2))
    return max(draw_count, clamped)


def build_snapshot(
    session_id: str,
    participants: list[Participant],
    draw_count: int,
    excluded_ids: list[str],
    created_at: str,
) -> dict[str, Any]:
    """커밋 대상 스냅샷. 시드뿐 아니라 명단·부서 편성·파라미터까지 포함해
    "추첨 직전 명단/인원수 바꿔치기" 의심을 원천 차단한다."""
    participants_sorted = sorted(participants, key=lambda p: p.id)
    departments = compute_department_groups(participants)
    return {
        "session_id": session_id,
        "participants": [
            {"id": p.id, "name": p.name, "team": p.team} for p in participants_sorted
        ],
        "departments": {name: sorted(ids) for name, ids in departments.items()},
        "draw_count": draw_count,
        "excluded_ids": sorted(set(excluded_ids)),
        "created_at": created_at,
    }


def _department_pass_rates(
    departments: dict[str, list[str]],
    pass_set: set[str],
    denom_sets: dict[str, set[str]],
) -> dict[str, float]:
    rates: dict[str, float] = {}
    for name, ids in departments.items():
        denom = len(denom_sets.get(name, set()))
        if denom == 0:
            rates[name] = 0.0
            continue
        numerator = len(pass_set.intersection(ids))
        rates[name] = numerator / denom
    return rates


def _compute_outcome(
    eligible_ids: list[str],
    seed: str,
    draw_count: int,
    departments: dict[str, list[str]],
) -> dict[str, Any]:
    """(참가 가능 id 목록, 시드, 당첨 인원수, 부서 편성) -> 순위/통과자/부서집계.

    draw 시점과 리빌 재계산 시점 양쪽에서 동일하게 호출되는 순수 함수다.
    """
    if draw_count > len(eligible_ids):
        raise FairnessError("당첨 인원수가 참가 가능 인원보다 많습니다")

    ranking = sorted(eligible_ids, key=lambda pid: (-_score(seed, pid), pid))

    finalist_count = min(resolve_finalist_count(draw_count), len(ranking))
    r1_count = min(max(R1_PASS_COUNT, finalist_count), len(ranking))

    r1_pass = ranking[:r1_count]
    r2_pass = ranking[:finalist_count]
    winners = ranking[:draw_count]

    r1_pass_set = set(r1_pass)
    r2_pass_set = set(r2_pass)

    all_group_sets = {name: set(ids) for name, ids in departments.items()}
    round_pass_rate = {
        1: _department_pass_rates(departments, r1_pass_set, all_group_sets),
        2: _department_pass_rates(
            departments,
            r2_pass_set,
            {name: s & r1_pass_set for name, s in all_group_sets.items()},
        ),
    }

    return {
        "ranking": ranking,
        "winners": winners,
        "round_pass_ids": {1: r1_pass, 2: r2_pass, 3: winners},
        "department_pass_rate": round_pass_rate,
        "finalist_count": finalist_count,
    }


def compute_draw(
    session_id: str,
    participants: list[Participant],
    draw_count: int,
    excluded_ids: list[str] | None = None,
    created_at: str | None = None,
    seed: str | None = None,
) -> DrawResult:
    """커밋-리빌 사이클의 1~3단계: 시드 생성 -> 커밋 -> 순위/통과자 계산.

    반환된 DrawResult.seed·commit은 리빌 전까지 seed는 비공개, commit만 공개한다.
    """
    excluded_ids = list(excluded_ids or [])
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    seed = seed or secrets.token_hex(32)

    excluded = set(excluded_ids)
    eligible_ids = [p.id for p in participants if p.id not in excluded]

    snapshot = build_snapshot(session_id, participants, draw_count, excluded_ids, created_at)
    commit = compute_commit(seed, snapshot)

    outcome = _compute_outcome(eligible_ids, seed, draw_count, snapshot["departments"])

    return DrawResult(
        seed=seed,
        commit=commit,
        snapshot=snapshot,
        winners=outcome["winners"],
        ranking=outcome["ranking"],
        round_pass_ids=outcome["round_pass_ids"],
        department_pass_rate=outcome["department_pass_rate"],
        finalist_count=outcome["finalist_count"],
        created_at=created_at,
    )


def reveal(draw: DrawResult) -> DrawResult:
    """seed·snapshot을 공개 상태로 전환한다 (레이스 종료 후 호출)."""
    draw.revealed = True
    draw.revealed_at = datetime.now(timezone.utc).isoformat()
    return draw


def recompute_from_reveal(seed: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """리빌된 seed+snapshot만으로 커밋·순위·통과자·부서집계·당첨자를 처음부터
    재계산한다. verify 엔드포인트/검증 페이지가 서버 저장값을 신뢰하지 않고
    이 함수(및 JS 이식본)로 독립 검증할 수 있도록 분리했다."""
    commit = compute_commit(seed, snapshot)
    excluded = set(snapshot.get("excluded_ids", []))
    eligible_ids = [p["id"] for p in snapshot["participants"] if p["id"] not in excluded]
    outcome = _compute_outcome(
        eligible_ids, seed, snapshot["draw_count"], snapshot["departments"]
    )
    return {"commit": commit, **outcome}


def verify_draw(draw: DrawResult) -> bool:
    """저장된 DrawResult가 자기 자신의 seed+snapshot으로부터 재계산 가능한지 검증."""
    if not draw.revealed:
        raise FairnessError("리빌되지 않은 추첨은 검증할 수 없습니다")
    recomputed = recompute_from_reveal(draw.seed, draw.snapshot)
    return (
        recomputed["commit"] == draw.commit
        and recomputed["winners"] == draw.winners
        and recomputed["ranking"] == draw.ranking
        and recomputed["round_pass_ids"] == draw.round_pass_ids
    )
