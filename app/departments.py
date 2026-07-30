from __future__ import annotations

from app.models import Participant

MIN_GROUPS = 5
MAX_GROUPS = 8


def compute_department_groups(
    participants: list[Participant],
    max_groups: int = MAX_GROUPS,
) -> dict[str, list[str]]:
    """실제 부서(team)를 예측 대상으로 쓰기 좋은 5~8개 그룹으로 자동 편성한다.

    부서 수가 max_groups 이하면 그대로 쓴다. 넘으면 가장 인원이 적은 두 그룹을
    반복해서 합쳐(그리디 병합) 목표 그룹 수까지 줄인다. 부서명이 비어있는
    참가자는 "미지정" 그룹으로 모은다.
    """
    by_team: dict[str, list[str]] = {}
    for p in participants:
        team = p.team.strip() or "미지정"
        by_team.setdefault(team, []).append(p.id)

    groups = [(name, sorted(ids)) for name, ids in by_team.items()]
    groups.sort(key=lambda g: (-len(g[1]), g[0]))

    if len(groups) <= max_groups:
        return {name: ids for name, ids in groups}

    while len(groups) > max_groups:
        groups.sort(key=lambda g: (len(g[1]), g[0]))
        a_name, a_ids = groups.pop(0)
        b_name, b_ids = groups.pop(0)
        merged_name = f"{a_name}·{b_name}"
        groups.append((merged_name, sorted(a_ids + b_ids)))

    groups.sort(key=lambda g: (-len(g[1]), g[0]))
    return {name: ids for name, ids in groups}


def group_of(groups: dict[str, list[str]], participant_id: str) -> str | None:
    for name, ids in groups.items():
        if participant_id in ids:
            return name
    return None
