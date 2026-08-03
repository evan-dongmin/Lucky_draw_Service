"""Prediction Agent: 무손실 확신도 배분 예측 게임.

핵심 설계(기획안 §4 참조):
- 도박 요소 없음. 참가자는 확신도 100을 3라운드에 배분하고, 적중 시에만
  점수를 얻는다(틀려도 0점, 마이너스 없음). 잔액 차감·파산 개념이 없다.
- 확신도 배분(alloc)은 해당 라운드가 잠기기 전까지 언제든 재배분 가능.
- 예측 대상(target)은 "해당 라운드의 선택 창이 열린 동안만" 설정 가능하다.
  미리 걸어두고 잊는 경로를 만들지 않기 위해서다. 창이 열리기 전 설정
  시도는 거부된다.
- 선택 시간 내 미선택 시 커밋된 시드에서 파생된 무작위 배정으로 채운다
  (0점 처리하지 않음 -- 아무도 게임에서 완전히 밀려나지 않는다).
- 채점은 "저장된 예측(및 무작위 배정) + Fairness 결과 -> 점수"의 순수
  함수라서 언제든 재계산으로 검증·복구할 수 있다.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

MIN_ALLOC = 10
TOTAL_ALLOC = 100
ROUND_WEIGHTS = {1: 1.0, 2: 1.5, 3: 2.5}
ROUNDS = (1, 2, 3)

RoundState = str  # "pending" | "open" | "locked"


class PredictionError(ValueError):
    pass


def _autopick_index(seed: str, participant_id: str, round_index: int, candidate_count: int) -> int:
    material = f"autopick:{participant_id}:{round_index}"
    digest = hmac.new(seed.encode("utf-8"), material.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest, "big") % candidate_count


def top_k_by_rate(rates: dict[str, float], k: int) -> set[str]:
    """비율 내림차순, 동률은 이름 오름차순으로 정렬해 상위 k개를 결정론적으로 뽑는다."""
    ordered = sorted(rates.items(), key=lambda kv: (-kv[1], kv[0]))
    return {name for name, _ in ordered[:k]}


@dataclass
class PredictionCard:
    participant_id: str
    alloc: dict[int, int] = field(default_factory=lambda: {1: 34, 2: 33, 3: 33})
    target: dict[int, str | None] = field(default_factory=lambda: {1: None, 2: None, 3: None})
    is_auto: dict[int, bool] = field(default_factory=lambda: {1: False, 2: False, 3: False})
    locked: dict[int, bool] = field(default_factory=lambda: {1: False, 2: False, 3: False})
    gain: dict[int, int] = field(default_factory=dict)
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "alloc": dict(self.alloc),
            "target": dict(self.target),
            "is_auto": dict(self.is_auto),
            "locked": dict(self.locked),
            "gain": dict(self.gain),
            "score": self.score,
        }


class PredictionEngine:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.cards: dict[str, PredictionCard] = {}
        self.round_state: dict[int, RoundState] = {r: "pending" for r in ROUNDS}
        self.round_candidates: dict[int, list[str]] = {r: [] for r in ROUNDS}
        self.round_share: dict[int, dict[str, float]] = {}

    # -- 카드 관리 ------------------------------------------------------

    def get_or_create_card(self, participant_id: str) -> PredictionCard:
        if participant_id not in self.cards:
            self.cards[participant_id] = PredictionCard(participant_id=participant_id)
        return self.cards[participant_id]

    def set_allocation(self, participant_id: str, alloc: dict[int, int]) -> PredictionCard:
        card = self.get_or_create_card(participant_id)
        if set(alloc.keys()) != set(ROUNDS):
            raise PredictionError("확신도는 1·2·3라운드 모두 지정해야 합니다")
        for r in ROUNDS:
            if card.locked[r] and alloc[r] != card.alloc[r]:
                raise PredictionError(f"{r}라운드는 이미 잠겨 확신도를 바꿀 수 없습니다")
        for r in ROUNDS:
            if alloc[r] < MIN_ALLOC:
                raise PredictionError(f"{r}라운드 확신도는 최소 {MIN_ALLOC} 이상이어야 합니다")
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

    def lock_round(self, round_index: int, seed: str) -> None:
        """선택 창을 잠근다. 미선택 참가자는 시드 파생 값으로 자동 배정한다."""
        candidates = self.round_candidates[round_index]
        if not candidates:
            raise PredictionError(f"{round_index}라운드 후보가 설정되지 않았습니다")
        for card in self.cards.values():
            if card.target[round_index] is None:
                idx = _autopick_index(seed, card.participant_id, round_index, len(candidates))
                card.target[round_index] = candidates[idx]
                card.is_auto[round_index] = True
            card.locked[round_index] = True
        self.round_state[round_index] = "locked"

    # -- 채점 -------------------------------------------------------------

    def score_round(self, round_index: int, hit_set: set[str]) -> dict[str, float]:
        """라운드 채점. 순수 함수적 성질을 위해 언제든 재호출 가능하도록
        gain[round_index]를 매번 새로 계산해 score에 반영한다(중복 가산 방지)."""
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

        weight = ROUND_WEIGHTS[round_index]
        for card in self.cards.values():
            target = card.target[round_index]
            if target is None:
                gain = 0
            elif target in hit_set:
                bonus = 1 + (1 - share.get(target, 0.0)) if share else 1.0
                gain = int(card.alloc[round_index] * weight * bonus)
            else:
                gain = 0
            previous = card.gain.get(round_index, 0)
            card.score += gain - previous
            card.gain[round_index] = gain
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
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        """저장된 스냅샷으로 현재 인스턴스를 제자리에서 복원한다(참조 유지).
        채점 결과(gain/score)까지 그대로 담겨 있으므로 재계산 없이 그대로
        복원되며, 상태머신(round_state/round_candidates)도 함께 복원되어
        진행 중이던 선택 창이 서버 재시작 후에도 유지된다."""
        self.reset()
        for pid, card_data in data.get("cards", {}).items():
            card = PredictionCard(participant_id=pid)
            card.alloc = {int(k): v for k, v in card_data["alloc"].items()}
            card.target = {int(k): v for k, v in card_data["target"].items()}
            card.is_auto = {int(k): v for k, v in card_data["is_auto"].items()}
            card.locked = {int(k): v for k, v in card_data["locked"].items()}
            card.gain = {int(k): v for k, v in card_data.get("gain", {}).items()}
            card.score = card_data["score"]
            self.cards[pid] = card
        for r, s in data.get("round_state", {}).items():
            self.round_state[int(r)] = s
        for r, c in data.get("round_candidates", {}).items():
            self.round_candidates[int(r)] = c
        for r, s in data.get("round_share", {}).items():
            self.round_share[int(r)] = s
