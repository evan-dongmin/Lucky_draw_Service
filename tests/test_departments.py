from app.departments import MAX_GROUPS, compute_department_groups, group_of
from app.models import Participant


def _participants(team_sizes: dict[str, int]) -> list[Participant]:
    participants = []
    i = 0
    for team, size in team_sizes.items():
        for _ in range(size):
            i += 1
            participants.append(Participant(id=f"P{i:03d}", name=f"이름{i}", team=team))
    return participants


def test_few_teams_kept_as_is():
    participants = _participants({"개발팀": 10, "영업팀": 5, "인사팀": 3})
    groups = compute_department_groups(participants)
    assert set(groups.keys()) == {"개발팀", "영업팀", "인사팀"}
    assert len(groups["개발팀"]) == 10


def test_many_teams_merged_to_max_groups():
    team_sizes = {f"팀{i}": 3 for i in range(15)}
    participants = _participants(team_sizes)
    groups = compute_department_groups(participants)
    assert len(groups) <= MAX_GROUPS

    all_ids = set()
    for ids in groups.values():
        all_ids.update(ids)
    assert all_ids == {p.id for p in participants}


def test_no_participant_lost_or_duplicated_during_merge():
    team_sizes = {f"팀{i}": (i % 4) + 1 for i in range(20)}
    participants = _participants(team_sizes)
    groups = compute_department_groups(participants)

    seen: dict[str, int] = {}
    for ids in groups.values():
        for pid in ids:
            seen[pid] = seen.get(pid, 0) + 1

    assert seen == {p.id: 1 for p in participants}


def test_blank_team_becomes_unassigned_group():
    participants = [Participant(id="P1", name="홍길동", team="")]
    groups = compute_department_groups(participants)
    assert groups == {"미지정": ["P1"]}


def test_group_of_lookup():
    participants = _participants({"개발팀": 3, "영업팀": 3})
    groups = compute_department_groups(participants)
    assert group_of(groups, "P001") == "개발팀"
    assert group_of(groups, "P999") is None
