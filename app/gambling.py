"""Gambling Agent: 승인된 사이버머니 갬블링 계층.

`predictions.py`(확신도 배분, 무손실)와는 별도의 **선택 모드**다. 세션은
`prediction_mode`가 "confidence"면 predictions.py를, "gambling"이면 이
모듈을 쓴다 -- 동시에 두 게임을 참가자에게 강요하지 않는다.

기존 확신도 배분과 다른 점: 이건 진짜로 잃을 수 있다. 정산은 경마·카트
레이스에 흔한 **패리뮤추얼(pari-mutuel) 방식**을 쓴다 -- 이긴 쪽에게
"판돈의 몇 배"를 고정 지급하는 게 아니라, **그 라운드에 걸린 전체 판돈**을
맞춘 참가자들이 자신이 건 금액 비율대로 나눠 갖는다. 이 설계를 고른 이유:

- 지급 총액이 항상 걷힌 판돈과 정확히 같다 -- 그 이상 나갈 수 없으므로
  "운영자가 감당 못 할 지급"이 구조적으로 발생하지 않는다(하우스 없음,
  참가자끼리 판돈을 재분배할 뿐).
- 배당률이 실시간 선택 분포에서 자연스럽게 나온다(쏠리면 배당이 낮아지고,
  쏠리지 않으면 배당이 높아진다) -- MC가 실황으로 해설할 좋은 소재가 된다.
- 잔액은 항상 0 이상으로 유지된다(건 만큼만 잃으므로 마이너스가 없다).

이 계층은 실물 경품 당첨자 결정과 **완전히 분리**된다. `BetCard.balance`는
오직 이 게임 내부의 점수판일 뿐, `draw.winners`는 fairness.py가 커밋된
시드만으로 계산하며 이 모듈은 그 계산에 어떤 영향도 주지 않는다("맞히면
사이버머니를 얻는" 예측 대상일 뿐, 배팅 행위 자체가 결과를 바꾸지 않는다).

파산 방지 완충: 라운드 2·3이 새로 열릴 때마다 소액의 "라운드 보너스 칩"을
지급해, 초반에 전 재산을 잃어도 남은 라운드에서 완전히 게임에서 밀려나지
않게 한다(기획안 §4의 "누구도 수학적으로 탈락하지 않는다" 철학을 그대로
갬블링에도 적용한 것).

보상 내역 기록: 참가자가 자기 폰에서 "이번 라운드에 왜 얼마를 받았는지"를
직접 확인할 수 있어야 하므로(사용자 요청), 라운드별 지급 내역을
`BetCard.rewards`에 항목별로 쪼개서 남긴다(결승선 통과분 / 팀 순위
보너스 / 결선 당첨분). 잔액에 이미 합산 반영된 값의 사본일 뿐이라
정산 로직에는 영향을 주지 않는다.

(예전에 있던 "카트 업그레이드 상점"은 제거했다 -- 실제 순위에 영향을 줄
수 없는 순수 코스메틱이라 게임 선택지로서 의미가 없다는 사용자 판단.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STARTING_BALANCE = 500
ROUND_BONUS_CHIPS = 80  # 라운드 2·3 개방 시 전원에게 지급(완전 파산 방지)
ROUNDS = (1, 2, 3)

# 라운드 보상(사용자 요청: "가장 많이 살아남은 부서원을 가진 팀 순으로
# 차등 보상 + 결승선을 통과한 개인에게도 별도 보상") -- 이미 fairness.py가
# 계산해둔 결과를 읽어서 지급할 뿐이므로 추첨 계산 자체에는 관여하지 않는다.
FINISH_REWARD = 40  # 그 라운드 통과선을 넘은 개인 전원
TEAM_RANK_REWARDS = [100, 70, 40]  # 그 라운드 통과율 1~3위 부서 소속 통과자에게 순위별 추가 지급(4위 이하 0)
FINAL_WIN_REWARD = 300  # 결선(R3) 최종 당첨자에게 지급

RoundState = str  # "pending" | "open" | "locked"


class GamblingError(ValueError):
    pass


@dataclass
class BetCard:
    participant_id: str
    balance: int = STARTING_BALANCE
    bets: dict[int, tuple[str, int] | None] = field(
        default_factory=lambda: {1: None, 2: None, 3: None}
    )
    locked: dict[int, bool] = field(default_factory=lambda: {r: False for r in ROUNDS})
    net: dict[int, int] = field(default_factory=dict)  # 라운드별 순손익(표시용, 정산 후 채워짐)
    payout: dict[int, int] = field(default_factory=dict)  # 라운드별 실제 지급액(내부용, 잔액 재계산 멱등성 보장)
    # 라운드별 성과 보상 내역(표시 전용) -- {round: {"finish": 40, "team_bonus": 100,
    # "team_rank": 1, "final": 300}}. 베팅 손익(net)과 달리 "결승선을 통과해서/
    # 우리 팀이 잘해서" 받은 몫을 참가자 폰에서 항목별로 보여주기 위한 사본이다.
    rewards: dict[int, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "balance": self.balance,
            "bets": {
                str(r): (None if b is None else {"target": b[0], "amount": b[1]})
                for r, b in self.bets.items()
            },
            "locked": {str(r): v for r, v in self.locked.items()},
            "net": {str(r): v for r, v in self.net.items()},
            "payout": {str(r): v for r, v in self.payout.items()},
            "rewards": {str(r): dict(v) for r, v in self.rewards.items()},
        }


class GamblingEngine:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.cards: dict[str, BetCard] = {}
        self.round_state: dict[int, RoundState] = {r: "pending" for r in ROUNDS}
        self.round_candidates: dict[int, list[str]] = {r: [] for r in ROUNDS}
        self.round_pool: dict[int, dict[str, int]] = {r: {} for r in ROUNDS}
        self.round_resolved: dict[int, bool] = {r: False for r in ROUNDS}

    # -- 카드 관리 ------------------------------------------------------

    def get_or_create_card(self, participant_id: str) -> BetCard:
        if participant_id not in self.cards:
            self.cards[participant_id] = BetCard(participant_id=participant_id)
        return self.cards[participant_id]

    # -- 라운드 창 관리 ---------------------------------------------------

    def open_round(self, round_index: int, candidates: list[str]) -> None:
        if not candidates:
            raise GamblingError("후보 목록이 비어 있습니다")
        self.round_state[round_index] = "open"
        self.round_candidates[round_index] = list(candidates)
        self.round_pool[round_index] = {c: 0 for c in candidates}
        if round_index in (2, 3):
            for card in self.cards.values():
                card.balance += ROUND_BONUS_CHIPS

    def place_bet(self, participant_id: str, round_index: int, target: str, amount: int) -> BetCard:
        if self.round_state.get(round_index) != "open":
            raise GamblingError(f"{round_index}라운드 베팅 창이 열려 있지 않습니다")
        candidates = self.round_candidates[round_index]
        if target not in candidates:
            raise GamblingError(f"{target}은(는) {round_index}라운드 후보가 아닙니다")
        if amount < 0:
            raise GamblingError("베팅 금액은 0 이상이어야 합니다")

        card = self.get_or_create_card(participant_id)
        if card.locked[round_index]:
            raise GamblingError(f"{round_index}라운드는 이미 잠겨 베팅을 바꿀 수 없습니다")

        # 재베팅 지원: 이번 라운드 안에서 이미 건 돈이 있으면 먼저 되돌린 뒤
        # 새로 건다(같은 라운드에서 마음이 바뀌는 것은 자연스러운 흐름이다).
        previous = card.bets[round_index]
        if previous is not None:
            prev_target, prev_amount = previous
            card.balance += prev_amount
            self.round_pool[round_index][prev_target] -= prev_amount
            card.bets[round_index] = None

        if amount > card.balance:
            raise GamblingError(f"보유 사이버머니({card.balance})보다 많이 걸 수 없습니다")

        if amount > 0:
            card.balance -= amount
            self.round_pool[round_index][target] += amount
            card.bets[round_index] = (target, amount)
        return card

    def lock_round(self, round_index: int) -> None:
        """선택 창을 잠근다. 확신도 배분과 달리 **자동 베팅은 하지 않는다**
        -- 참가자 동의 없이 가상의 돈이라도 마음대로 거는 것은 예측 게임의
        "미선택 시 무작위 배정"과는 성격이 다른 문제이기 때문이다. 베팅하지
        않은 참가자는 그 라운드를 그냥 구경만 한다(잔액 변화 없음)."""
        self.round_state[round_index] = "locked"
        for card in self.cards.values():
            card.locked[round_index] = True

    # -- 정산 -------------------------------------------------------------

    def resolve_round(self, round_index: int, hit_set: set[str]) -> dict[str, Any]:
        """패리뮤추얼 정산: 전체 판돈을 승자 판돈 비율로 승자들에게 분배한다.

        베팅 시점에 이미 `amount`만큼 잔액에서 차감돼 있으므로, 여기서는
        "지급액(payout)"만 balance에 더하면 된다 -- 진 경우 payout=0(이미
        낸 돈을 돌려받지 못할 뿐, 추가로 차감하지 않는다). `card.payout`에
        직전 지급액을 기록해두고 그 차이만 반영하는 방식으로, 서버 재시작
        후 같은 hit_set으로 재호출해도 중복 지급되지 않게 한다(순수 함수적
        재계산 가능성 보장 -- predictions.py의 score_round와 동일한 패턴)."""
        pool = self.round_pool.get(round_index, {})
        total_pool = sum(pool.values())
        winning_pool = sum(v for k, v in pool.items() if k in hit_set)

        for card in self.cards.values():
            bet = card.bets[round_index]
            new_payout = 0
            new_net = 0
            if bet is not None:
                target, amount = bet
                if target in hit_set and winning_pool > 0:
                    new_payout = round(amount / winning_pool * total_pool)
                    new_net = new_payout - amount
                else:
                    new_net = -amount
            previous_payout = card.payout.get(round_index, 0)
            card.balance += new_payout - previous_payout
            card.payout[round_index] = new_payout
            card.net[round_index] = new_net
        self.round_resolved[round_index] = True

        return self._odds_payload(round_index)

    # -- 라운드 보상: 통과한 개인 + 부서 순위별 차등 보상 + 최종 당첨자 -----

    def award_round_rewards(
        self,
        passed_ids: set[str],
        ranked_dept_ids: list[set[str]] | None = None,
        round_index: int | None = None,
    ) -> dict[str, int]:
        """이미 fairness.py가 계산해둔 결과(통과자·부서별 통과율 순위)를 읽어서
        지급할 뿐, 추첨 계산 자체에는 관여하지 않는다. 카드가 없는(한 번도
        참여하지 않은) 참가자에게는 카드를 새로 만들지 않는다 -- 리더보드에
        아무 행동도 안 한 유령 항목이 뜨는 것을 막기 위함.

        ranked_dept_ids: 그 라운드 통과율 순위대로(1위부터) 정렬된 "부서
        소속 id 집합" 리스트. TEAM_RANK_REWARDS[i]가 i번째 순위 부서 소속
        통과자에게 추가 지급된다(리스트 길이를 넘는 순위는 추가 보상 없음).

        round_index를 주면 각 카드의 `rewards[round_index]`에 항목별 내역
        (finish / team_bonus / team_rank)을 남긴다 -- 참가자 폰에서 "이번
        라운드에 왜 얼마를 받았는지"를 보여주기 위한 표시 전용 사본이다.
        """
        bonus_by_pid: dict[str, int] = {}
        rank_by_pid: dict[str, int] = {}
        if ranked_dept_ids:
            for rank, dept_ids in enumerate(ranked_dept_ids[: len(TEAM_RANK_REWARDS)]):
                reward = TEAM_RANK_REWARDS[rank]
                for pid in dept_ids:
                    bonus_by_pid[pid] = reward
                    rank_by_pid[pid] = rank + 1

        granted: dict[str, int] = {}
        for pid in passed_ids:
            card = self.cards.get(pid)
            if card is None:
                continue
            team_bonus = bonus_by_pid.get(pid, 0)
            amount = FINISH_REWARD + team_bonus
            card.balance += amount
            granted[pid] = amount
            if round_index is not None:
                detail = {"finish": FINISH_REWARD, "total": amount}
                if team_bonus:
                    detail["team_bonus"] = team_bonus
                    detail["team_rank"] = rank_by_pid[pid]
                card.rewards[round_index] = detail
        return granted

    def award_final_rewards(
        self, winner_ids: set[str], round_index: int | None = None
    ) -> dict[str, int]:
        granted: dict[str, int] = {}
        for pid in winner_ids:
            card = self.cards.get(pid)
            if card is None:
                continue
            card.balance += FINAL_WIN_REWARD
            granted[pid] = FINAL_WIN_REWARD
            if round_index is not None:
                card.rewards[round_index] = {
                    "final": FINAL_WIN_REWARD,
                    "total": FINAL_WIN_REWARD,
                }
        return granted

    # -- 실시간 배당률/판돈 조회 --------------------------------------------

    def live_odds(self, round_index: int) -> dict[str, Any]:
        """선택 창이 열려 있는 동안(또는 정산 후) 그 시점까지의 판돈 기준
        배당률. 정산 전/후 동일한 패리뮤추얼 공식을 쓰므로, 정산 직전 마지막
        조회값과 정산 결과의 배당률이 항상 일치한다."""
        return self._odds_payload(round_index)

    def _odds_payload(self, round_index: int) -> dict[str, Any]:
        pool = self.round_pool.get(round_index, {})
        total_pool = sum(pool.values())
        odds = {
            target: (round(total_pool / amount, 2) if amount > 0 else None)
            for target, amount in pool.items()
        }
        return {"round": round_index, "pool": dict(pool), "total_pool": total_pool, "odds": odds}

    # -- 조회 ---------------------------------------------------------------

    def leaderboard(self, top_n: int = 10) -> list[BetCard]:
        ordered = sorted(self.cards.values(), key=lambda c: (-c.balance, c.participant_id))
        return ordered[:top_n]

    # -- 영속화(장애 복구용) ---------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "cards": {pid: card.to_dict() for pid, card in self.cards.items()},
            "round_state": {str(r): s for r, s in self.round_state.items()},
            "round_candidates": {str(r): c for r, c in self.round_candidates.items()},
            "round_pool": {str(r): dict(p) for r, p in self.round_pool.items()},
            "round_resolved": {str(r): v for r, v in self.round_resolved.items()},
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        """저장된 스냅샷으로 현재 인스턴스를 제자리에서 복원한다(참조 유지).
        정산 결과(net/balance)까지 그대로 담겨 있으므로 재계산 없이 복원되며,
        진행 중이던 베팅 창 상태도 서버 재시작 후 그대로 유지된다."""
        self.reset()
        for pid, card_data in data.get("cards", {}).items():
            card = BetCard(participant_id=pid)
            card.balance = card_data["balance"]
            card.bets = {
                int(k): (None if v is None else (v["target"], v["amount"]))
                for k, v in card_data["bets"].items()
            }
            card.locked = {int(k): v for k, v in card_data["locked"].items()}
            card.net = {int(k): v for k, v in card_data.get("net", {}).items()}
            card.payout = {int(k): v for k, v in card_data.get("payout", {}).items()}
            card.rewards = {int(k): dict(v) for k, v in card_data.get("rewards", {}).items()}
            self.cards[pid] = card
        for r, s in data.get("round_state", {}).items():
            self.round_state[int(r)] = s
        for r, c in data.get("round_candidates", {}).items():
            self.round_candidates[int(r)] = c
        for r, p in data.get("round_pool", {}).items():
            self.round_pool[int(r)] = dict(p)
        for r, v in data.get("round_resolved", {}).items():
            self.round_resolved[int(r)] = v
