"""Prediction Agent: 무손실 예측 게임(확신도 배분 + 순위 차등 채점).

핵심 설계(기획안 §4 참조):
- 도박 요소 없음. 참가자는 확신도 100을 3라운드에 배분하고, 적중 시에만
  점수를 얻는다(틀려도 0점, 마이너스 없음). 잔액 차감·파산 개념이 없다.
- **순위 차등 채점**(사용자 요청). 예전에는 "정답 집합(R1은 통과율 상위
  2개 부서, R2는 1위 부서)에 들었나"만 봐서 대다수가 0점이었다. 지금은
  고른 대상이 통과율 순위에서 몇 등이었는지에 따라 배점의 일정 비율을
  받는다 -- 1위 100%, 2위 60%, ... 그 아래는 최소 5%(참여 보상). 빗나가도
  0점이 아니므로 첫 라운드에 어긋난 참가자가 폰을 내려놓지 않는다.
  RANK_RATIOS/FLOOR_RATIO만 고치면 곡선 전체를 조절할 수 있다.
- **소수파 보너스는 1위를 정확히 맞힌 경우에만** 붙는다. 차등 구간까지
  보너스를 주면 "틀렸는데 아무도 안 골라서 더 받는" 이상한 결과가 나온다.
- **성과 점수**: 예측과 별개로, 그 라운드를 통과한 개인과 통과율 상위
  부서 소속 통과자에게 점수를 얹는다(갬블링 모드에 있던 보상 체계를 예측
  점수 단위로 옮겨온 것). 이게 팀 대항전의 실체다 -- 우리 부서가 많이
  살아남을수록 부서원 전체 점수가 오르고, 그게 곧 경품 확률이 된다.
- 확신도 배분(alloc)은 해당 라운드가 잠기기 전까지 언제든 재배분 가능.
  R1·R2에는 최소 배점(MIN_ALLOC)이 걸려 있어 초반을 통째로 버릴 수 없지만,
  **R3에는 하한이 없어 나머지를 전부 몰아줄 수 있다**(최대 80). R3는 배점
  가중치도 가장 높아서(ROUND_WEIGHTS), 앞 라운드를 망쳐도 마지막에
  몰아주기로 역전할 수 있는 구조다.
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

MIN_ALLOC = 10  # R1·R2에만 적용된다(R3는 하한 없음 -- 몰아주기 허용)
TOTAL_ALLOC = 100
MIN_ALLOC_ROUNDS = (1, 2)
ROUND_WEIGHTS = {1: 1.0, 2: 1.5, 3: 3.0}
ROUNDS = (1, 2, 3)

# 순위별 지급 비율(1위부터). 리스트 길이를 넘는 순위는 FLOOR_RATIO를 받는다.
# 1위를 맞히는 것과 대충 찍는 것 사이에 20배 격차를 둬서, 참여 보상을
# 깔아도 리더보드 변별력이 죽지 않게 했다.
RANK_RATIOS = [1.0, 0.6, 0.35, 0.2, 0.1]
FLOOR_RATIO = 0.05  # "참여만 해도 들어오는" 최소 보상

# 성과 점수(예측 적중 여부와 무관하게 쌓인다). 확신도 33을 R1에 걸어
# 1위를 맞혔을 때가 약 33~66점이므로, 팀 1위 20점은 "무시 못 하지만
# 예측을 대체하지도 않는" 크기다.
FINISH_POINTS = 8  # 그 라운드 통과선을 넘은 개인 전원
TEAM_RANK_POINTS = [20, 14, 8]  # 통과율 1~3위 부서 소속 통과자(4위 이하 0)
FINAL_WIN_POINTS = 60  # 결선(R3) 최종 당첨자

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
    alloc: dict[int, int] = field(default_factory=lambda: {1: 34, 2: 33, 3: 33})
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
            "alloc": dict(self.alloc),
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

    def set_allocation(self, participant_id: str, alloc: dict[int, int]) -> PredictionCard:
        card = self.get_or_create_card(participant_id)
        if set(alloc.keys()) != set(ROUNDS):
            raise PredictionError("확신도는 1·2·3라운드 모두 지정해야 합니다")
        for r in ROUNDS:
            if card.locked[r] and alloc[r] != card.alloc[r]:
                raise PredictionError(f"{r}라운드는 이미 잠겨 확신도를 바꿀 수 없습니다")
        for r in MIN_ALLOC_ROUNDS:
            if alloc[r] < MIN_ALLOC:
                raise PredictionError(f"{r}라운드 확신도는 최소 {MIN_ALLOC} 이상이어야 합니다")
        if alloc[3] < 0:
            raise PredictionError("3라운드 확신도는 0 이상이어야 합니다")
        if sum(alloc.values()) != TOTAL_ALLOC:
            raise PredictionError(f"확신도 합계는 {TOTAL_ALLOC}이어야 합니다")
        card.alloc = dict(alloc)
        return card

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

    def _default_target(self, card: PredictionCard, round_index: int, seed: str) -> str:
        """미선택 참가자의 자동 배정값.

        R1·R2는 대상이 부서라서 **자기 소속 부서**가 가장 자연스러운
        기본값이다(자기 팀을 응원한다는 뜻이 되고, 폰 없이도 납득 가능한
        결과가 나온다). 소속 부서가 이번 라운드 후보에 없으면(부서 병합
        등) 시드 파생 무작위로 폴백한다. R3는 대상이 결선 진출자 개인이라
        "자기 팀"에 해당하는 것이 없으므로 항상 시드 파생 무작위다."""
        candidates = self.round_candidates[round_index]
        if round_index in MIN_ALLOC_ROUNDS:
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
    ) -> dict[str, float]:
        """라운드 채점: 예측 점수(순위 차등) + 성과 점수.

        ranked_targets는 그 라운드 결과 순위대로 정렬된 대상 목록이다
        (R1·R2는 통과율 순 부서명, R3는 결승 등수 순 결선 진출자 id).
        고른 대상이 여기서 몇 번째인지에 따라 rank_ratio()만큼 배점을 받고,
        목록 밖이면 참여 보상(FLOOR_RATIO)을 받는다 -- 어느 경우에도 0점이
        되지 않는 것이 이 설계의 핵심이다.

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

        for card in self.cards.values():
            detail: dict[str, int] = {}
            target = card.target[round_index]

            predict_gain = 0
            if target is not None:
                rank = rank_of.get(target)
                ratio = rank_ratio(rank) if rank is not None else FLOOR_RATIO
                base = card.alloc[round_index] * weight * ratio
                # 소수파 보너스는 "1위를 정확히 맞혔을 때"만 -- 아무도 안 고른
                # 대상이 1위가 되면 최대 2배까지 간다(막판 뒤집기의 연료).
                if rank == 1 and share:
                    base *= 1 + (1 - share.get(target, 0.0))
                predict_gain = int(base)
                detail["hit_rank"] = rank if rank is not None else 0
                detail["predict"] = predict_gain

            perf_gain = 0
            if card.participant_id in passed_ids:
                perf_gain += FINISH_POINTS
                detail["finish"] = FINISH_POINTS
                team_points = team_points_by_pid.get(card.participant_id, 0)
                if team_points:
                    perf_gain += team_points
                    detail["team_bonus"] = team_points
                    detail["team_rank"] = team_rank_by_pid[card.participant_id]
            if card.participant_id in final_winner_ids:
                perf_gain += FINAL_WIN_POINTS
                detail["final"] = FINAL_WIN_POINTS

            gain = predict_gain + perf_gain
            detail["total"] = gain
            previous = card.gain.get(round_index, 0)
            card.score += gain - previous
            card.gain[round_index] = gain
            card.rewards[round_index] = detail
        return share

    # -- 조회 ---------------------------------------------------------------

    def leaderboard(self, top_n: int = 10) -> list[PredictionCard]:
        ordered = sorted(self.cards.values(), key=lambda c: (-c.score, c.participant_id))
        return ordered[:top_n]

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
            card.alloc = {int(k): v for k, v in card_data["alloc"].items()}
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
