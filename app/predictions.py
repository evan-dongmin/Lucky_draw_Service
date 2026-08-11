"""Prediction Agent: 무손실 예측 게임(라운드당 1픽 + 순위 차등 채점).

핵심 설계(기획안 §4 참조, 2026-08-07 단순화 -- 사용자 피드백):
- 도박 요소 없음. 참가자는 라운드마다 승리 예측 대상을 하나씩 고르고,
  적중 시에만 점수를 얻는다(틀려도 0점, 마이너스 없음).
- **예전엔 확신도 100점을 3라운드에 참가자가 직접 배분해야 했다.**
  "누구를 고를지"와 "얼마를 걸지"를 동시에 판단해야 해서, 선택 창이
  짧게 열리는 라이브 이벤트에는 불필요한 마찰이었다(사용자 피드백).
  지금은 라운드마다 **누구를 고를지만** 정하면 되고, 각 라운드는 항상
  동일한 배점(ROUND_BASE_POINTS)을 걸고 겨룬다 -- 월드컵 예측 게임처럼
  누구나 바로 이해하는 방식.
- **막판 역전 드라마는 확신도 없이도 그대로 유지된다.** ROUND_WEIGHTS가
  이미 결선(R3)에 3배 가중치를 주므로, 개인이 확신도를 몰아주지 않아도
  R3 하나로 순위가 크게 뒤집히는 구조는 동일하다.
- **순위 차등 채점**(사용자 요청). 고른 대상이 그 라운드 결과 순위에서
  몇 등이었는지에 따라 배점의 일정 비율을 받는다 -- 1위 100%, 2위 60%,
  ... 그 아래는 최소 5%(참여 보상). 빗나가도 0점이 아니므로 첫 라운드에
  어긋난 참가자가 폰을 내려놓지 않는다. RANK_RATIOS/FLOOR_RATIO만 고치면
  곡선 전체를 조절할 수 있다.
- **소수파 보너스는 1위를 정확히 맞힌 경우에만** 붙는다. 차등 구간까지
  보너스를 주면 "틀렸는데 아무도 안 골라서 더 받는" 이상한 결과가 나온다.
- **성과 점수**: 예측과 별개로, 그 라운드를 통과한 개인과 통과율 상위
  부서 소속 통과자에게 점수를 얹는다. 이게 팀 대항전의 실체다 -- 우리
  부서가 많이 살아남을수록 부서원 전체 점수가 오르고, 그게 곧 경품
  확률이 된다.
- 예측 대상(target)은 "해당 라운드의 선택 창이 열린 동안만" 설정 가능하다.
  미리 걸어두고 잊는 경로를 만들지 않기 위해서다.
- 선택 시간 내 미선택 시 자동 배정한다(0점 처리하지 않음). R1·R2는
  **자기 소속 부서**가 기본값이고(사용자 요청 -- 폰을 안 든 사람에게
  가장 자연스러운 선택이자, 자기 팀 응원이라는 서사와도 맞는다),
  R3는 결선 진출자 개인이 대상이라 "자기 팀"에 해당하는 것이 없으므로
  커밋된 시드에서 파생된 무작위 배정으로 채운다.
- 채점은 "저장된 예측(및 자동 배정) + Fairness 결과 -> 점수"의 순수
  함수라서 언제든 재계산으로 검증·복구할 수 있다.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

from . import characters

ROUND_WEIGHTS = {1: 1.0, 2: 1.5, 3: 3.0}
ROUNDS = (1, 2, 3)
DEPARTMENT_ROUNDS = (1, 2)  # 대상이 부서인 라운드(R3는 결선 진출자 개인)

# 라운드마다 동일하게 걸리는 배점(예전 확신도 100점 배분의 자리를
# 대신한다 -- 개인이 나눠 걸지 않고, 라운드마다 이 값을 그대로 놓고
# 겨룬다). ROUND_WEIGHTS와 곱해져 최종 배점이 된다.
#
# 사용자 피드백("예측으로 얻는 포인트가 너무 적다")에 따라 100 -> 300으로
# 올렸다. 이제 1위 적중은 R1 300 / R2 450 / R3 900점이라, 예측이 리더보드를
# 이끄는 주력이라는 게 숫자로도 분명해진다(성과 점수는 그 위에 얹히는
# 팀플레이 보너스 레이어로 남는다 -- 아래 상수들 참고).
# **이 값 하나만 바꾸면 예측 점수 전체가 비례해서 움직인다.**
ROUND_BASE_POINTS = 300

# 순위별 지급 비율(1위부터). 리스트 길이를 넘는 순위는 FLOOR_RATIO를 받는다.
# 1위를 맞히는 것과 대충 찍는 것 사이에 20배 격차를 둬서, 참여 보상을
# 깔아도 리더보드 변별력이 죽지 않게 했다.
RANK_RATIOS = [1.0, 0.6, 0.35, 0.2, 0.1]
FLOOR_RATIO = 0.05  # "참여만 해도 들어오는" 최소 보상

# 성과 점수(예측 적중 여부와 무관하게 쌓인다). R1을 1위로 맞히면
# 300점(ROUND_BASE_POINTS x ROUND_WEIGHTS[1])이므로, 팀 1위 45점은
# "무시 못 하지만 예측을 대체하지도 않는" 크기다.
# 실시간 통계에서 "소수파(💎)"로 표시할 기준: 균등 배분(1/후보수) 대비
# 이 비율 미만이면 소수파다. 절대 배수로 자르면 후보가 많을수록 전부
# 소수파가 되어 표시가 무의미해진다(live_stats 주석 참고).
MINORITY_SHARE_RATIO = 0.6

FINISH_POINTS = 20  # 그 라운드 통과선을 넘은 개인 전원
TEAM_RANK_POINTS = [45, 30, 18]  # 통과율 1~3위 부서 소속 통과자(4위 이하 0)
FINAL_WIN_POINTS = 150  # 결선(R3) 최종 당첨자

# 예측을 손수 고른 참가자에게 붙는 배수(사용자 요청, 2026-08-10).
# 미선택(자동 배정)도 적중하면 예측 점수를 100% 받는다는 원칙(참여
# 페널티 없음)은 그대로 두되, 직접 고른 사람에게는 이 배수만큼 더 준다.
# ability_bonus(카트 능력)와 독립적인 항목으로 분리해서 계산·표시한다 --
# 두 배수를 하나로 뭉치면 "왜 이만큼 더 받았는지"를 참가자가 알 수 없다.
#
# 1.1(+10%)로 시작했다가 **1.4(+40%)로 올렸다**(사용자 요청: "참여에 따른
# 혜택을 더 크게 해 줘"). 10%는 리더보드에서 체감되지 않았다 -- 소수파
# 보너스(최대 2배)나 라운드 가중치(최대 3배)에 묻히는 크기라, "폰을 들
# 이유"로 작동하지 못했다. 40%면 세 라운드 누적으로 벌어지는 차이가
# 눈에 띄면서도, 여전히 **무엇을 맞혔는가**(순위 차등 20배 격차)가
# **참여했는가**보다 크게 남는다.
MANUAL_PREDICT_MULTIPLIER = 1.4

RoundState = str  # "pending" | "open" | "locked"


class PredictionError(ValueError):
    pass


def _autopick_index(seed: str, participant_id: str, round_index: int, candidate_count: int) -> int:
    material = f"autopick:{participant_id}:{round_index}"
    digest = hmac.new(seed.encode("utf-8"), material.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest, "big") % candidate_count


def rank_targets_by_rate(rates: dict[str, float]) -> list[str]:
    """비율 내림차순, 동률은 이름 오름차순으로 정렬한 순위 목록(1위부터).

    동률 처리를 이름순으로 고정해 둔 덕분에, 같은 입력이면 서버를 몇 번
    재시작해도 항상 같은 순위가 나온다(채점의 재계산 가능성 보장).
    """
    return [name for name, _ in sorted(rates.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---------------------------------------------------------------------------
# 동점 처리 (2026-08-11, 사용자 요청)
#
# 250명이 고르는 선택지는 R1·R2가 부서 5~8개, R3가 결선 진출자 5~10명뿐이라
# **동점은 우연이 아니라 매 게임 반드시 생긴다**(실제 게임에서 상위 5명 중
# 4명이 같은 점수였다). 그런데 예전에는 `participant_id` 오름차순으로 갈랐다 --
# 사번이 빠른 사람이 경품을 받는다는 뜻이고, 무대에서 설명할 수 없는 기준이다.
#
# 이제 "예측을 얼마나 잘 했는가"로 가른다. 배점이 가장 큰 3라운드부터 본다.
# **획득 점수가 아니라 적중 등수를 쓴다** -- 획득 점수에는 소수파 보너스·카트
# 능력·직접 선택 배수가 이미 곱해져 있어서 "예측을 잘했나"가 아니라 "부가
# 요소가 좋았나"를 비교하게 된다.
# ---------------------------------------------------------------------------

TIEBREAK_ROUNDS = (3, 2, 1)
_NO_HIT_RANK = 9_999  # 순위 밖(적중 실패)은 항상 뒤로


def hit_rank_of(card: "PredictionCard", round_index: int) -> int:
    """그 라운드에 고른 대상이 실제 몇 등이었나(낮을수록 잘 맞힌 것).

    채점 내역의 `hit_rank`는 순위 밖을 0으로 적어두므로 뒤로 보낸다."""
    detail = card.rewards.get(round_index) or {}
    rank = int(detail.get("hit_rank") or 0)
    return rank if rank > 0 else _NO_HIT_RANK


def manual_round_count(card: "PredictionCard") -> int:
    """직접 고른 라운드 수(자동 배정 제외). 폰을 든 사람을 우대한다."""
    return sum(1 for r in (1, 2, 3) if card.is_auto.get(r) is False and card.target.get(r))


def ranking_key(card: "PredictionCard") -> tuple[Any, ...]:
    """리더보드 정렬 키. 총점 -> R3 -> R2 -> R1 적중 -> 직접 선택 수 -> 사번.

    마지막 사번은 **완전 동점일 때 순서를 재현 가능하게 만드는 용도**일 뿐이다
    (같은 입력이면 서버를 재시작해도 같은 순서). 여기까지 왔다는 건 세 라운드
    예측이 전부 같고 직접 선택 수까지 같다는 뜻이라, 표시상으로는 공동 등수로
    묶인다(ranked_leaderboard 참고)."""
    return (
        -card.score,
        *(hit_rank_of(card, r) for r in TIEBREAK_ROUNDS),
        -manual_round_count(card),
        card.participant_id,
    )


def _tiebreak_notes(ordered: list["PredictionCard"]) -> dict[str, str]:
    """같은 점수 그룹 안에서 순서를 가른 근거를 사람별 한 줄로 만든다.

    그룹 안에서 **가장 먼저 값이 갈린 기준 하나**만 보여준다. 여러 줄을 띄우면
    무대에서 읽히지 않는다."""
    notes: dict[str, str] = {}
    group: list[PredictionCard] = []

    def flush(members: list[PredictionCard]) -> None:
        if len(members) < 2:
            return
        for round_index in TIEBREAK_ROUNDS:
            values = [hit_rank_of(c, round_index) for c in members]
            if len(set(values)) > 1:
                for card, value in zip(members, values):
                    hit = "적중 실패" if value == _NO_HIT_RANK else f"적중 {value}등"
                    notes[card.participant_id] = f"{round_index}R {hit}"
                return
        counts = [manual_round_count(c) for c in members]
        if len(set(counts)) > 1:
            for card, value in zip(members, counts):
                notes[card.participant_id] = f"직접 선택 {value}개 라운드"
            return
        for card in members:
            notes[card.participant_id] = "완전 동점"

    for card in ordered:
        if group and group[0].score != card.score:
            flush(group)
            group = []
        group.append(card)
    flush(group)
    return notes


def rank_ratio(rank: int | None) -> float:
    """1-based 순위 -> 배점 비율. 순위 밖(None)이면 0."""
    if rank is None or rank < 1:
        return 0.0
    if rank <= len(RANK_RATIOS):
        return RANK_RATIOS[rank - 1]
    return FLOOR_RATIO


@dataclass
class PredictionCard:
    participant_id: str
    target: dict[int, str | None] = field(default_factory=lambda: {1: None, 2: None, 3: None})
    is_auto: dict[int, bool] = field(default_factory=lambda: {1: False, 2: False, 3: False})
    locked: dict[int, bool] = field(default_factory=lambda: {1: False, 2: False, 3: False})
    gain: dict[int, int] = field(default_factory=dict)
    score: int = 0
    # 라운드별 점수 내역(표시 전용). {round: {"hit_rank": 2, "predict": 30,
    # "finish": 8, "team_bonus": 20, "team_rank": 1, "final": 60, "total": 118}}
    # -- "이번 라운드에 왜 몇 점을 받았는지"가 폰에서 바로 보여야 한다는
    # 사용자 요청. gain/score에 이미 반영된 값의 사본일 뿐이라 채점 로직에는
    # 영향을 주지 않는다.
    rewards: dict[int, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "target": dict(self.target),
            "is_auto": dict(self.is_auto),
            "locked": dict(self.locked),
            "gain": dict(self.gain),
            "score": self.score,
            "rewards": {str(r): dict(v) for r, v in self.rewards.items()},
        }


class PredictionEngine:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.cards: dict[str, PredictionCard] = {}
        self.round_state: dict[int, RoundState] = {r: "pending" for r in ROUNDS}
        self.round_candidates: dict[int, list[str]] = {r: [] for r in ROUNDS}
        self.round_share: dict[int, dict[str, float]] = {}
        # participant_id -> 소속 부서명. R1·R2 미선택 시 자동 배정의 기본값이자,
        # 성과 점수 계산에 쓰인다. open_round(1) 시점에 채워진다.
        self.department_by_pid: dict[str, str] = {}

    # -- 카드 관리 ------------------------------------------------------

    def get_or_create_card(self, participant_id: str) -> PredictionCard:
        if participant_id not in self.cards:
            self.cards[participant_id] = PredictionCard(participant_id=participant_id)
        return self.cards[participant_id]

    def enroll_all(self, department_by_pid: dict[str, str]) -> None:
        """명단 전원에게 카드를 만들어 둔다(사용자 요청).

        예전에는 모바일로 참여(join)한 사람만 카드가 생겨서, 폰을 안 든
        사람은 리더보드에 아예 존재하지 않아 경품 대상에서 통째로 빠졌다.
        이제는 명단에 있기만 하면 카드가 생기고, R1·R2는 자기 부서가 기본
        선택으로 들어가므로 **아무것도 안 해도 최소한의 경품 가능성은
        확보**된다. 폰을 든 사람은 더 잘 고르고 확신도를 몰아써서 그 위로
        올라가는 구조다(참여 유인은 유지, 배제는 없음)."""
        self.department_by_pid = dict(department_by_pid)
        for pid in department_by_pid:
            self.get_or_create_card(pid)

    # -- 라운드 창 관리 ---------------------------------------------------

    def open_round(self, round_index: int, candidates: list[str]) -> None:
        if not candidates:
            raise PredictionError("후보 목록이 비어 있습니다")
        self.round_state[round_index] = "open"
        self.round_candidates[round_index] = list(candidates)

    def set_target(self, participant_id: str, round_index: int, target: str) -> PredictionCard:
        if self.round_state.get(round_index) != "open":
            raise PredictionError(f"{round_index}라운드 선택 창이 열려 있지 않습니다")
        candidates = self.round_candidates[round_index]
        if target not in candidates:
            raise PredictionError(f"{target}은(는) {round_index}라운드 후보가 아닙니다")
        card = self.get_or_create_card(participant_id)
        card.target[round_index] = target
        card.is_auto[round_index] = False
        return card

    def live_distribution(self, round_index: int) -> dict[str, float]:
        """선택 창이 아직 열려 있는 동안(잠기기 전) 지금까지의 선택 분포를
        읽기 전용으로 계산한다. score_round와 같은 규칙(명시적으로 고른
        사람만 집계, 자동배정은 제외)을 쓰므로, 창이 닫히는 순간의 값이
        score_round가 계산할 분포와 정확히 일치한다. 상태를 바꾸지 않는
        순수 조회 함수라 몇 번을 호출해도 안전하다(스테이지/모바일 화면이
        선택이 있을 때마다 실시간으로 조회해 "표가 몰립니다" 연출에 쓴다)."""
        explicit_targets = [
            card.target[round_index]
            for card in self.cards.values()
            if card.target[round_index] is not None and not card.is_auto[round_index]
        ]
        if not explicit_targets:
            return {}
        total = len(explicit_targets)
        counts: dict[str, float] = {}
        for t in explicit_targets:
            counts[t] = counts.get(t, 0) + 1
        return {k: v / total for k, v in counts.items()}

    def live_stats(self, round_index: int, candidates: list[str] | None = None) -> dict[str, Any]:
        """선택 창이 열려 있는 동안의 실시간 집계 -- 무대·폰이 "표가 어디로
        몰리는지"와 "어디가 소수파인지"를 보여주는 데 쓴다(사용자 요청).

        `live_distribution`이 비율만 돌려주는 것과 달리 인원수·참여율·소수파
        배수까지 함께 낸다. 비율만으로는 "3명 중 2명(67%)"과 "200명 중
        134명(67%)"이 구분되지 않아 판단 근거가 못 된다.

        `minority_bonus[target]`은 **그 대상이 1위가 됐을 때** 예측 점수에
        곱해질 소수파 배수다(`score_round`의 `1 + (1 - share)`와 같은 식).
        아무도 안 고른 곳이 2.0배로 가장 크다 -- 역배를 노리는 사람에게
        이게 보이면 선택이 한쪽으로만 쏠리지 않는다.

        아직 아무도 안 고른 후보도 목록에 넣으려면 `candidates`를 넘긴다
        (그래야 "0명 · 2.0배"인 진짜 소수파가 화면에서 사라지지 않는다).
        상태를 바꾸지 않는 순수 조회 함수다.
        """
        counts: dict[str, int] = {name: 0 for name in (candidates or [])}
        chosen = 0
        for card in self.cards.values():
            target = card.target[round_index]
            if target is None or card.is_auto[round_index]:
                continue
            counts[target] = counts.get(target, 0) + 1
            chosen += 1

        distribution = {name: (n / chosen if chosen else 0.0) for name, n in counts.items()}

        # "소수파"는 **균등 배분 대비 상대적으로** 판단해야 한다.
        # 절대 배수(1 + (1 - share))로 자르면 후보가 많을수록 전부 소수파가
        # 된다 -- 후보 8개가 고루 갈리면 각 share가 0.125라 배수가 전부
        # 1.875여서, 임계값을 1.8로 두면 여덟 팀 모두에 💎가 붙어 표시가
        # 아무 의미도 갖지 못한다(실제로 데모에서 그렇게 나왔다).
        # 균등 배분(1/후보수)의 MINORITY_SHARE_RATIO 미만인 후보만 소수파다.
        even_share = 1.0 / len(counts) if counts else 0.0
        threshold = even_share * MINORITY_SHARE_RATIO

        return {
            "round": round_index,
            "distribution": distribution,
            "counts": counts,
            # 직접 고른 사람 수 / 카드가 있는 전체 인원(= 명단 전원)
            "chosen": chosen,
            "eligible": len(self.cards),
            "minority_bonus": {
                name: round(1 + (1 - share), 2) for name, share in distribution.items()
            },
            # 표가 눈에 띄게 덜 몰린 후보. 아직 아무도 안 골랐으면(chosen=0)
            # 비교할 기준 자체가 없으므로 아무것도 소수파로 치지 않는다.
            "is_minority": {
                name: bool(chosen > 0 and share < threshold)
                for name, share in distribution.items()
            },
        }

    def _default_target(self, card: PredictionCard, round_index: int, seed: str) -> str:
        """미선택 참가자의 자동 배정값.

        R1·R2는 대상이 부서라서 **자기 소속 부서**가 가장 자연스러운
        기본값이다(자기 팀을 응원한다는 뜻이 되고, 폰 없이도 납득 가능한
        결과가 나온다). 소속 부서가 이번 라운드 후보에 없으면(부서 병합
        등) 시드 파생 무작위로 폴백한다. R3는 대상이 결선 진출자 개인이라
        "자기 팀"에 해당하는 것이 없으므로 항상 시드 파생 무작위다."""
        candidates = self.round_candidates[round_index]
        if round_index in DEPARTMENT_ROUNDS:
            own = self.department_by_pid.get(card.participant_id)
            if own in candidates:
                return own
        idx = _autopick_index(seed, card.participant_id, round_index, len(candidates))
        return candidates[idx]

    def lock_round(self, round_index: int, seed: str) -> None:
        """선택 창을 잠근다. 미선택 참가자는 _default_target으로 채운다."""
        candidates = self.round_candidates[round_index]
        if not candidates:
            raise PredictionError(f"{round_index}라운드 후보가 설정되지 않았습니다")
        for card in self.cards.values():
            if card.target[round_index] is None:
                card.target[round_index] = self._default_target(card, round_index, seed)
                card.is_auto[round_index] = True
            card.locked[round_index] = True
        self.round_state[round_index] = "locked"

    # -- 채점 -------------------------------------------------------------

    def score_round(
        self,
        round_index: int,
        ranked_targets: list[str],
        passed_ids: set[str] | None = None,
        ranked_dept_ids: list[set[str]] | None = None,
        final_winner_ids: set[str] | None = None,
        character_by_pid: dict[str, str] | None = None,
    ) -> dict[str, float]:
        """라운드 채점: 예측 점수(순위 차등) + 성과 점수.

        ranked_targets는 그 라운드 결과 순위대로 정렬된 대상 목록이다
        (R1·R2는 통과율 순 부서명, R3는 결승 등수 순 결선 진출자 id).
        고른 대상이 여기서 몇 번째인지에 따라 rank_ratio()만큼 배점을 받고,
        목록 밖이면 참여 보상(FLOOR_RATIO)을 받는다 -- 어느 경우에도 0점이
        되지 않는 것이 이 설계의 핵심이다.

        character_by_pid를 넘기면 참가자가 고른 카트 능력의 배수가 예측·성과
        점수에 적용된다(작업계획서 §12-3, 배수 정의는 app/characters.py).
        **레이스 순위는 여전히 전혀 건드리지 않는다** -- 능력은 사람이 고르는
        값이라 순위에 영향을 주면 추첨 공정성이 깨지기 때문이다.

        순수 함수적 성질을 위해 언제든 재호출 가능하도록 gain[round_index]를
        매번 새로 계산해 score에 반영한다(중복 가산 방지). 성과 점수도 같은
        gain에 합산되므로 재호출 시 함께 멱등하게 재계산된다.
        """
        explicit_targets = [
            card.target[round_index]
            for card in self.cards.values()
            if card.target[round_index] is not None and not card.is_auto[round_index]
        ]
        share: dict[str, float] = {}
        if explicit_targets:
            total = len(explicit_targets)
            for t in explicit_targets:
                share[t] = share.get(t, 0) + 1
            share = {k: v / total for k, v in share.items()}
        self.round_share[round_index] = share

        rank_of = {name: i + 1 for i, name in enumerate(ranked_targets)}
        weight = ROUND_WEIGHTS[round_index]

        # 성과 점수 조회표를 미리 만들어 둔다(참가자 수 x 부서 수 순회 방지).
        passed_ids = passed_ids or set()
        team_points_by_pid: dict[str, int] = {}
        team_rank_by_pid: dict[str, int] = {}
        for rank, dept_ids in enumerate((ranked_dept_ids or [])[: len(TEAM_RANK_POINTS)]):
            for pid in dept_ids:
                team_points_by_pid[pid] = TEAM_RANK_POINTS[rank]
                team_rank_by_pid[pid] = rank + 1
        final_winner_ids = final_winner_ids or set()

        character_by_pid = character_by_pid or {}

        for card in self.cards.values():
            detail: dict[str, int] = {}
            target = card.target[round_index]

            # 카트 능력 배수(작업계획서 §12-3). 레이스 결과가 아니라 **예측
            # 점수 규칙만** 비튼다. 미선택자는 중립 1.0배라 안 고른 사람이
            # 유리해지는 일은 없다. plain_* 값을 함께 계산해 두고 마지막에
            # 차액을 detail["ability_bonus"]로 남긴다 -- 폰에서 "능력으로 몇
            # 점 더 받았는지"가 보여야 선택이 실감난다.
            character_id = character_by_pid.get(card.participant_id)
            ability = characters.effect_for(character_id)

            predict_gain = 0
            plain_predict = 0
            predict_gain_no_manual = 0
            if target is not None:
                rank = rank_of.get(target)
                ratio = rank_ratio(rank) if rank is not None else FLOOR_RATIO
                base = ROUND_BASE_POINTS * weight * ratio
                # 소수파 보너스는 "1위를 정확히 맞혔을 때"만 -- 아무도 안 고른
                # 대상이 1위가 되면 최대 2배까지 간다(막판 뒤집기의 연료).
                minority = 1.0
                if rank == 1 and share:
                    minority = 1 + (1 - share.get(target, 0.0))
                plain_predict = int(base * minority)

                if rank == 1 and share:
                    base *= minority + ability.minority_bonus_add
                multiplier = 1.0
                if round_index in ability.predict_rounds:
                    multiplier *= ability.predict_multiplier
                if rank == 1:
                    multiplier *= ability.top_hit_multiplier
                elif rank in (2, 3):
                    multiplier *= ability.runner_up_multiplier
                if rank is None or rank > len(RANK_RATIOS):
                    # FLOOR_RATIO(참여 보상)를 받은 경우
                    multiplier *= ability.floor_multiplier
                predict_gain_no_manual = int(base * multiplier)
                predict_gain = predict_gain_no_manual
                if not card.is_auto[round_index]:
                    predict_gain = int(predict_gain_no_manual * MANUAL_PREDICT_MULTIPLIER)
                    manual_bonus = predict_gain - predict_gain_no_manual
                    if manual_bonus:
                        detail["manual_bonus"] = manual_bonus
                detail["hit_rank"] = rank if rank is not None else 0
                detail["predict"] = predict_gain

            perf_gain = 0
            plain_perf = 0
            if card.participant_id in passed_ids:
                finish_points = int(FINISH_POINTS * ability.finish_multiplier)
                perf_gain += finish_points
                plain_perf += FINISH_POINTS
                detail["finish"] = finish_points
                team_points = team_points_by_pid.get(card.participant_id, 0)
                if team_points:
                    boosted_team = int(team_points * ability.team_multiplier)
                    perf_gain += boosted_team
                    plain_perf += team_points
                    detail["team_bonus"] = boosted_team
                    detail["team_rank"] = team_rank_by_pid[card.participant_id]
            if card.participant_id in final_winner_ids:
                perf_gain += FINAL_WIN_POINTS
                plain_perf += FINAL_WIN_POINTS
                detail["final"] = FINAL_WIN_POINTS

            ability_bonus = (predict_gain_no_manual + perf_gain) - (plain_predict + plain_perf)
            if character_id and ability_bonus:
                detail["ability_bonus"] = ability_bonus

            gain = predict_gain + perf_gain
            detail["total"] = gain
            previous = card.gain.get(round_index, 0)
            card.score += gain - previous
            card.gain[round_index] = gain
            card.rewards[round_index] = detail
        return share

    # -- 조회 ---------------------------------------------------------------

    def round_target_summary(
        self, round_index: int, ranked_targets: list[str]
    ) -> list[dict[str, Any]]:
        """채점이 끝난 라운드를 **대상별로** 요약한다(무대 발표 화면 전용).

        "어느 팀을 골랐으면 몇 점을 받았나"를 등수 순으로 보여주기 위한 값이라
        추첨·채점에는 전혀 관여하지 않는 순수 조회 함수다. `score_round`가
        끝난 뒤에 호출해야 소수파 배수(`round_share`)가 채워져 있다.

        `points`는 §5-1 점수표와 같은 기준값(카트 능력·직접 선택 보너스를
        빼고, 소수파 배수만 반영)이고, `manual_points`는 여기에 직접 선택
        보너스까지 붙은 값이다. 개인별 실제 획득액은 고른 카트 능력에 따라
        더 달라지므로 화면에는 "기준"임을 함께 밝혀야 한다.
        """
        share = self.round_share.get(round_index, {})
        weight = ROUND_WEIGHTS[round_index]
        counts: dict[str, int] = {}
        for card in self.cards.values():
            target = card.target[round_index]
            if target is None or card.is_auto[round_index]:
                continue
            counts[target] = counts.get(target, 0) + 1

        rows: list[dict[str, Any]] = []
        for index, name in enumerate(ranked_targets):
            rank = index + 1
            minority = 1.0
            if rank == 1 and share:
                minority = 1 + (1 - share.get(name, 0.0))
            points = int(ROUND_BASE_POINTS * weight * rank_ratio(rank) * minority)
            rows.append(
                {
                    "name": name,
                    "rank": rank,
                    "chosen": counts.get(name, 0),
                    "points": points,
                    "manual_points": int(points * MANUAL_PREDICT_MULTIPLIER),
                    "minority": round(minority, 2),
                }
            )
        return rows

    def leaderboard(self, top_n: int = 10) -> list[PredictionCard]:
        ordered = sorted(self.cards.values(), key=ranking_key)
        return ordered[:top_n]

    def rank_of(self, participant_id: str) -> int | None:
        """participant_id의 현재 포인트(점수) 순위(1-based). leaderboard()와
        동일한 정렬 규칙을 써서 상위 N에 안 걸린 사람도 정확한 순위를 알 수
        있다(사용자 요청: 모바일에서 "내 포인트 순위" 표시). 카드가 없으면 None."""
        if participant_id not in self.cards:
            return None
        ordered = sorted(self.cards.values(), key=ranking_key)
        for idx, card in enumerate(ordered, start=1):
            if card.participant_id == participant_id:
                return idx
        return None

    def ranked_leaderboard(self, top_n: int = 10) -> list[dict[str, Any]]:
        """시상대·리더보드 표시용. 등수(공동 등수 포함)와 "무엇으로 갈렸는지"를
        함께 돌려준다(2026-08-11, 사용자 요청).

        - `rank`: 공동 등수를 반영한 1-based 등수. 완전 동점이면 같은 값을
          갖고, 그다음 사람은 그만큼 건너뛴다(공동 2등이 둘이면 다음은 4등).
        - `tiebreak_note`: 같은 점수인데 순서가 갈린 경우 그 근거 한 줄.
          **이걸 안 보여주면 관객이 반드시 의심한다** -- 같은 점수인데 누구는
          받고 누구는 못 받는 상황이 눈앞에서 벌어지기 때문이다.
        """
        ordered = sorted(self.cards.values(), key=ranking_key)[:top_n]
        notes = _tiebreak_notes(ordered)

        rows: list[dict[str, Any]] = []
        prev_key: tuple[Any, ...] | None = None
        rank = 0
        for idx, card in enumerate(ordered, start=1):
            key = ranking_key(card)[:-1]  # 사번은 등수 판정에서 뺀다
            if prev_key is None or key != prev_key:
                rank = idx  # 표준 경쟁 등수: 동점 다음은 인원수만큼 건너뛴다
                prev_key = key
            rows.append(
                {
                    "participant_id": card.participant_id,
                    "score": card.score,
                    "rank": rank,
                    "tiebreak_note": notes.get(card.participant_id, ""),
                }
            )
        return rows

    # -- 영속화(장애 복구용) ---------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "cards": {pid: card.to_dict() for pid, card in self.cards.items()},
            "round_state": {str(r): s for r, s in self.round_state.items()},
            "round_candidates": {str(r): c for r, c in self.round_candidates.items()},
            "round_share": {str(r): s for r, s in self.round_share.items()},
            "department_by_pid": dict(self.department_by_pid),
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        """저장된 스냅샷으로 현재 인스턴스를 제자리에서 복원한다(참조 유지).
        채점 결과(gain/score)까지 그대로 담겨 있으므로 재계산 없이 그대로
        복원되며, 상태머신(round_state/round_candidates)과 부서 매핑도 함께
        복원되어 진행 중이던 선택 창이 서버 재시작 후에도 유지된다."""
        self.reset()
        for pid, card_data in data.get("cards", {}).items():
            card = PredictionCard(participant_id=pid)
            card.target = {int(k): v for k, v in card_data["target"].items()}
            card.is_auto = {int(k): v for k, v in card_data["is_auto"].items()}
            card.locked = {int(k): v for k, v in card_data["locked"].items()}
            card.gain = {int(k): v for k, v in card_data.get("gain", {}).items()}
            card.score = card_data["score"]
            card.rewards = {int(k): dict(v) for k, v in card_data.get("rewards", {}).items()}
            self.cards[pid] = card
        for r, s in data.get("round_state", {}).items():
            self.round_state[int(r)] = s
        for r, c in data.get("round_candidates", {}).items():
            self.round_candidates[int(r)] = c
        for r, s in data.get("round_share", {}).items():
            self.round_share[int(r)] = s
        self.department_by_pid = dict(data.get("department_by_pid", {}))
