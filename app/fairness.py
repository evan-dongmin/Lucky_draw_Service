"""Fairness Agent: 커밋-리빌 추첨 + 부서 대항 중첩 생존 집합.

핵심 불변식(모든 draw_count·부서 분포에서 반드시 성립):
    winners(N) ⊆ round_pass_ids[2](F) ⊆ round_pass_ids[1](R1 통과)

부서 통과율은 표시·예측용 파생 지표일 뿐, 생존 여부를 결정하지 않는다.
생존은 항상 개별 상위 절단으로만 결정된다(부서 통째 탈락 금지).

**순위 산식(작업계획서 §12-4, 2026-08-08)**: 기본 순위는 여전히
HMAC(seed, participant_id)로 정하지만, 거기에 `race.py`가 시드로부터
결정론적으로 파생시킨 장애물 페널티를 더해 최종 순위를 만든다(레이스
연출에서 장애물에 맞는 것이 실제로 결과를 바꾼다). 두 값 다 시드에서만
파생되고 서로 순환 참조가 없으므로(장애물 배치는 순위와 무관), 기본 순위
→ 장애물 페널티 → 최종 순위 한 번의 패스로 계산이 끝난다. 커밋 시점에
전부 확정되며, 레이스 애니메이션은 그 확정된 결과로 수렴하는 연출일 뿐이다
(기존과 동일한 설계 원칙 -- 달라진 것은 "순위를 정하는 산식"뿐).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app import race
from app.departments import compute_department_groups
from app.models import DrawResult, Participant

R1_PASS_COUNT = 100

# **결승선 컷오프(작업계획서 §12-8, 2026-08-08, 사용자 요청)**: R1/R2는
# 순위(위 R1_PASS_COUNT/resolve_finalist_count)로 정해지는 후보군 중에서도,
# "1등이 결승선을 통과한 시점부터 이 초 안에 들어온 카트만" 실제로
# 통과한다. 순위 조건과 시간 조건 둘 다 만족해야 하는 AND 조건이다(사용자
# 확인: "순위 개념과 시간 제한 개념"). 그래도 다음 라운드/최종 당첨자가
# 텅 비는 사고를 막기 위해 최소 인원은 항상 보장한다(아래
# _apply_cutoff_window의 min_survivors).
R1_CUTOFF_WINDOW_SECONDS = 10.0
R2_CUTOFF_WINDOW_SECONDS = 5.0
# 결선(R3)도 "1등 통과 후 N초"를 쓰지만 **아무도 떨어뜨리지 않는다** --
# 창이 닫히면 레이스를 끝내고 결과 발표로 넘어갈 뿐이다(app/main.py의
# _run_race_phase 참고). 결선 결승선은 애초에 "정확히 N대가 넘도록" 놓이므로
# 창 길이를 바꿔도 당첨자는 달라질 수 없다.
#
# 2026-08-10에 "첫 차 통과 즉시 발표"로 0까지 줄였다가 **되돌렸다**(사용자
# 판단): 1등이 들어온 뒤의 이 몇 초가 나머지 결선 카트들이 차례로 결승선을
# 넘는 장면이라, 곧바로 끊으면 그 장면이 통째로 사라진다. 순위·점수 자체는
# 커밋 시점에 이미 확정돼 있어 창 길이와 무관하지만, **관객이 그 결과를
# 눈으로 확인하는 유일한 구간**이므로 연출상 남겨둔다.
R3_CUTOFF_WINDOW_SECONDS = 5.0


class FairnessError(ValueError):
    pass


def canonical_json(data: Any) -> str:
    """커밋 해시가 항상 같은 바이트열에서 나오도록 하는 정규화 직렬화.

    - 키를 재귀적으로 정렬한다 (json.dumps sort_keys=True는 중첩 dict에도 적용됨).
    - 구분자에 공백을 넣지 않는다.
    - ensure_ascii=False로 한글을 이스케이프하지 않는다.

    원래는 브라우저(`static/fairness.js`)가 같은 결과를 재현하도록 맞춘
    규칙이었다. 그 검증 페이지는 제거됐지만(작업계획서 §13-6) **규칙은
    그대로 지켜야 한다** -- 직렬화가 한 글자라도 달라지면 같은 입력에서
    다른 커밋 해시가 나와, 리빌 후 재계산(`recompute_from_reveal`)이
    실패하고 정상 추첨이 위변조로 오탐된다."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_commit(seed: str, snapshot: dict[str, Any]) -> str:
    payload = f"{seed}|{canonical_json(snapshot)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _score(seed: str, participant_id: str) -> int:
    digest = hmac.new(seed.encode("utf-8"), participant_id.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest, "big")


def _obstacle_adjusted_ranking(eligible_ids: list[str], seed: str) -> list[str]:
    """HMAC 기본 순위에 장애물 페널티를 더해 최종 순위를 만든다.

    기본 순위를 0(1등)..1(꼴찌) 소수 비율로 정규화한 뒤 장애물 페널티
    (같은 0..1 스케일, `race.total_obstacle_penalty`)를 더해 다시 정렬한다.
    페널티가 없는 카트끼리는 기본 순위가 그대로 유지된다(동점 시 기본
    순위 → id 순으로 결정론적 타이브레이크)."""
    base_ranking = sorted(eligible_ids, key=lambda pid: (-_score(seed, pid), pid))
    base_rank_index = {pid: i for i, pid in enumerate(base_ranking)}
    denom = max(1, len(base_ranking) - 1)

    def adjusted_key(pid: str) -> tuple[float, int, str]:
        base_fraction = base_rank_index[pid] / denom
        penalty = race.total_obstacle_penalty(seed, pid)
        return (base_fraction + penalty, base_rank_index[pid], pid)

    return sorted(eligible_ids, key=adjusted_key)


def _apply_cutoff_window(
    candidates: list[str],
    population: list[str],
    seed: str,
    round_index: int,
    pass_line_value: float,
    race_seconds: float | None,
    window_seconds: float,
    min_survivors: int,
) -> list[str]:
    """`candidates`(순위 기준 후보군, `population` 안에서의 등수 순으로
    정렬돼 있어야 한다) 중 "첫 통과자 + window_seconds" 안에 실제로
    결승선(`pass_line_value`)을 통과하는 카트만 남긴다.

    - `race_seconds`가 없으면(런북 밖에서 순위만 보고 싶은 호출 -- 대부분의
      기존 테스트) 컷오프를 건너뛰고 candidates를 그대로 돌려준다(예전
      "순위만으로 통과" 동작과 100% 동일).
    - **창은 "최소 window_seconds, 단 정원(min_survivors)이 찰 때까지"다.**
      아래 `cutoff_close_ratio` 참고 -- 통과자는 예외 없이 "마감 전에 실제로
      결승선을 넘은 카트"뿐이다.
    - 반환 순서는 항상 `candidates`의 원래 순위 순서를 유지한다.
    """
    if race_seconds is None or not candidates:
        return list(candidates)

    crossing = crossing_ratios(candidates, population, seed, round_index, pass_line_value)
    close_ratio = cutoff_close_ratio(
        crossing, race_seconds, window_seconds, min_survivors
    )
    if close_ratio is None:
        # 후보 전원이 결승선에 못 미침 -- 컷오프 기준점 자체가 없다.
        return list(candidates)

    within_window = [pid for pid in candidates if crossing[pid] <= close_ratio]
    if len(within_window) >= min_survivors:
        return within_window

    # 여기까지 왔다는 건 "결승선에 아예 못 닿는 카트"(crossing=inf)가 섞여
    # 정원을 못 채운다는 뜻이다. 다음 라운드가 텅 비는 사고만은 막는다.
    rescued = set(within_window)
    for pid in candidates:
        if len(rescued) >= min_survivors:
            break
        rescued.add(pid)
    return [pid for pid in candidates if pid in rescued]


def crossing_ratios(
    candidates: list[str],
    population: list[str],
    seed: str,
    round_index: int,
    pass_line_value: float,
) -> dict[str, float]:
    """후보별 "결승선을 넘는 진행률". 끝까지 못 넘으면 inf."""
    total = len(population)
    rank_index_of = {pid: i for i, pid in enumerate(population)}
    out: dict[str, float] = {}
    for pid in candidates:
        ratio = race.crossing_ratio(
            rank_index_of[pid], total, pass_line_value, pid, round_index, seed
        )
        out[pid] = ratio if ratio is not None else float("inf")
    return out


def cutoff_close_ratio(
    crossing: dict[str, float],
    race_seconds: float,
    window_seconds: float,
    min_survivors: int,
) -> float | None:
    """컷오프 창이 닫히는 진행률(0..1). 후보 전원이 결승선에 못 닿으면 None.

    **"1등 통과 + window_seconds"로 끝내지 않고, 정원(min_survivors)째 카트가
    통과하는 시점까지 늘린다**(2026-08-11, 사용자 버그 제보).

    예전에는 창을 5초로 딱 끊고, 모자란 인원을 순위 순으로 조용히 채워
    넣었다. 그래서 화면은 "4대 통과"인데 다음 라운드에는 5대가 올라가는
    모순이 그대로 보였다. 게다가 이건 예외가 아니라 **기본 동작**이었다 --
    250명·360초 실측에서 당첨 10명 설정이면 R2 후보 10대 중 실제 통과는
    약 5대뿐이고 나머지 절반이 전부 구제됐다.

    원인은 결승선 위치다. 결승선은 "F등 카트가 ratio=1.0에 정확히 닿는
    지점"에 놓이므로, **꼴찌 후보는 레이스가 끝나는 순간에야 선을 넘는다.**
    1등이 넘고 5초 안에 F대가 다 들어오는 상황은 애초에 성립할 수 없다.

    창을 늘리면 "통과한 카트만 진출한다"는 규칙이 화면과 결과 양쪽에서
    동시에 참이 된다. 정원이 후보 수와 같은 설정(당첨 10명 = 후보 10대)에서는
    창이 레이스 끝까지 늘어나 아무도 탈락하지 않는데, 이는 숨겨야 할 결함이
    아니라 **사실 그대로**다(그 설정에선 원래 아무도 떨어질 수 없다).
    """
    if not crossing:
        return None
    finite = sorted(r for r in crossing.values() if r != float("inf"))
    if not finite:
        return None

    close = finite[0] + window_seconds / race_seconds
    need = min(min_survivors, len(finite))
    if need > 0:
        # 정원째 카트가 들어올 때까지 기다린다(이미 그 전에 찼으면 그대로).
        close = max(close, finite[need - 1])
    # 진행률은 1.0을 넘지 않는다. 안 자르면 무대 카운트다운이 영영 0에
    # 닿지 않아 "🔒 통과 마감" 상태가 나오지 않는다(레이스가 먼저 끝난다).
    # 마지막 순간에 선을 넘는 카트는 `<=` 비교라 그대로 통과로 잡힌다.
    return min(close, 1.0)


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
    race_r1_seconds: float | None = None,
    race_r2_seconds: float | None = None,
) -> dict[str, Any]:
    """커밋 대상 스냅샷. 시드뿐 아니라 명단·부서 편성·파라미터까지 포함해
    "추첨 직전 명단/인원수 바꿔치기" 의심을 원천 차단한다.

    `race_r1_seconds`/`race_r2_seconds`(결승선 컷오프 계산에 쓰는 그
    라운드의 실제 레이스 시간)도 스냅샷에 포함한다 -- 리빌 후
    `recompute_from_reveal`이 이 스냅샷만으로 커밋 당시와 100% 동일한
    결과를 재현하려면, 컷오프 판정에 쓰인 타이밍 값도 함께 봉인돼 있어야
    한다."""
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
        "race_r1_seconds": race_r1_seconds,
        "race_r2_seconds": race_r2_seconds,
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
    race_r1_seconds: float | None = None,
    race_r2_seconds: float | None = None,
) -> dict[str, Any]:
    """(참가 가능 id 목록, 시드, 당첨 인원수, 부서 편성) -> 순위/통과자/부서집계.

    draw 시점과 리빌 재계산 시점 양쪽에서 동일하게 호출되는 순수 함수다.

    `race_r1_seconds`/`race_r2_seconds`(그 라운드의 실제 레이스 진행
    시간, 스타트 라이트 리드인 제외 -- `app/main.py`의 `_run_race_phase`가
    쓰는 것과 같은 값)를 주면 결승선 컷오프(§12-8)가 함께 적용된다. 안
    주면(대부분의 기존 테스트) 예전과 똑같이 순위만으로 통과가 정해진다.
    """
    if draw_count > len(eligible_ids):
        raise FairnessError("당첨 인원수가 참가 가능 인원보다 많습니다")

    # 순위 자체는 장애물 페널티 합(레인 일치로만 정해짐)으로 조정된다 --
    # 장애물이 트랙 어디에 놓이는지는 여기에 영향을 주지 않으므로,
    # "순위 -> 통과선 -> 장애물 위치"로 이어지는 순환 참조가 생기지 않는다
    # (app/race.py의 _hazard_specs 설명 참고).
    ranking = _obstacle_adjusted_ranking(eligible_ids, seed)
    total0 = len(ranking)

    finalist_count = min(resolve_finalist_count(draw_count), total0)
    r1_count = min(max(R1_PASS_COUNT, finalist_count), total0)

    # --- 1라운드: 순위 상위 r1_count 후보 중 "1등 결승 통과 + 10초" 컷오프.
    # 결승선(통과선) 자체는 순위 기준 후보군(r1_count) 그대로 유지한다 --
    # 실제 통과자 수가 컷오프로 줄어도 통과선 위치는 안 바뀌어야, 시간에
    # 밀려 탈락한 카트도 "원래는 통과권이었다"는 게 화면에서 그대로
    # 보인다(결승선을 늦게라도 넘는 모습 자체는 그대로 나온다).
    r1_candidates = ranking[:r1_count]
    r1_line = race.pass_line(r1_count, total0)
    r1_pass = _apply_cutoff_window(
        r1_candidates,
        ranking,
        seed,
        round_index=1,
        pass_line_value=r1_line,
        race_seconds=race_r1_seconds,
        window_seconds=R1_CUTOFF_WINDOW_SECONDS,
        min_survivors=finalist_count,  # R2가 항상 치러질 만큼은 보장
    )

    # --- 2라운드: R1 생존자 중 순위 상위 finalist_count 후보 + "1등 결승
    # 통과 + 5초" 컷오프. population이 r1_pass(=R1 생존자, r1_count보다
    # 적을 수 있음)라서 등수·통과선 모두 그 안에서 다시 정해진다(실제
    # R2 레이스가 이 인원으로 열리는 것과 동일한 기준).
    r2_population = r1_pass
    r2_candidate_count = min(finalist_count, len(r2_population))
    r2_candidates = r2_population[:r2_candidate_count]
    r2_line = race.pass_line(r2_candidate_count, len(r2_population))
    r2_pass = _apply_cutoff_window(
        r2_candidates,
        r2_population,
        seed,
        round_index=2,
        pass_line_value=r2_line,
        race_seconds=race_r2_seconds,
        window_seconds=R2_CUTOFF_WINDOW_SECONDS,
        min_survivors=min(draw_count, len(r2_population)),  # 당첨자 수만큼은 보장
    )

    # --- 결선: 컷오프 없음(사용자 요청 범위 밖) -- R2 생존자 중 순위
    # 그대로 상위 draw_count명이 최종 당첨자.
    winners = r2_pass[:draw_count]

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
        # 통과선(결승선) 계산 및 무대 화면의 "N/후보수" 실시간 카운터가
        # 쓰는, 컷오프 전 순위 기준 후보군 크기. round_pass_ids의 실제
        # 길이(컷오프 후)와는 다른 값일 수 있다.
        "round_candidate_count": {1: r1_count, 2: r2_candidate_count},
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
    race_r1_seconds: float | None = None,
    race_r2_seconds: float | None = None,
) -> DrawResult:
    """커밋-리빌 사이클의 1~3단계: 시드 생성 -> 커밋 -> 순위/통과자 계산.

    반환된 DrawResult.seed·commit은 리빌 전까지 seed는 비공개, commit만 공개한다.
    `race_r1_seconds`/`race_r2_seconds`는 결승선 컷오프(§12-8) 계산용 --
    `app/main.py`가 런북에서 그 라운드의 실제 레이스 시간을 구해 넘긴다.
    """
    excluded_ids = list(excluded_ids or [])
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    seed = seed or secrets.token_hex(32)

    excluded = set(excluded_ids)
    eligible_ids = [p.id for p in participants if p.id not in excluded]

    snapshot = build_snapshot(
        session_id, participants, draw_count, excluded_ids, created_at,
        race_r1_seconds=race_r1_seconds, race_r2_seconds=race_r2_seconds,
    )
    commit = compute_commit(seed, snapshot)

    outcome = _compute_outcome(
        eligible_ids, seed, draw_count, snapshot["departments"],
        race_r1_seconds=race_r1_seconds, race_r2_seconds=race_r2_seconds,
    )

    return DrawResult(
        seed=seed,
        commit=commit,
        snapshot=snapshot,
        winners=outcome["winners"],
        ranking=outcome["ranking"],
        round_pass_ids=outcome["round_pass_ids"],
        round_candidate_count=outcome["round_candidate_count"],
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
        eligible_ids,
        seed,
        snapshot["draw_count"],
        snapshot["departments"],
        race_r1_seconds=snapshot.get("race_r1_seconds"),
        race_r2_seconds=snapshot.get("race_r2_seconds"),
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
