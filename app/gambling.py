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

업그레이드 상점(순수 연출용): 사용자 요청으로 "라운드 통과/우승 보상 ->
업그레이드 구매"라는 소비 선택지를 추가했다. **"능력치 향상"이라는 표현을
그대로 구현하면 안 된다** -- 이 프로젝트의 핵심 신뢰 장치는 추첨 결과가
커밋 시점의 시드로 완전히 고정된다는 것이고(`/verify`에서 누구나 재계산
가능), 사이버머니로 실제 순위/통과 여부를 바꿀 수 있다면 그 장치가
깨진다. 그래서 "업그레이드"는 카트의 시각 효과(글로우 강도·오라 크기)만
바꾸는 **코스메틱 전용**이다 -- 레이스 계산에는 어떤 값도 넘기지 않는다
(팀 특수능력·개인 캐릭터 선택과 동일한 원칙).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STARTING_BALANCE = 500
ROUND_BONUS_CHIPS = 80  # 라운드 2·3 개방 시 전원에게 지급(완전 파산 방지)
ROUNDS = (1, 2, 3)

# 라운드 보상(사용자 요청: "우승한 팀"과 "결승선 통과한 개인"에게 사이버머니
# 지급) -- 이미 fairness.py가 계산해둔 결과를 읽어서 지급할 뿐이므로 추첨
# 계산 자체에는 관여하지 않는다.
FINISH_REWARD = 40  # 그 라운드 통과선을 넘은 개인 전원
TEAM_WIN_REWARD = 60  # 그 라운드 최고 통과율 부서 소속 통과자에게 추가 지급
FINAL_WIN_REWARD = 300  # 결선(R3) 최종 당첨자에게 지급

# 개인 카트 업그레이드: 레벨업마다 비용이 오른다(순수 코스메틱 -- 글로우
# 강도만 바뀐다).
PERSONAL_UPGRADE_COST = [100, 200, 300]
MAX_PERSONAL_UPGRADE_LEVEL = len(PERSONAL_UPGRADE_COST)

# 팀 업그레이드: 부서 소속 참가자들이 십시일반으로 판돈을 모으면(공동
# 투자) 이 임계값마다 팀 전체의 시각 효과 레벨이 오른다.
TEAM_UPGRADE_THRESHOLD = 500
MAX_TEAM_UPGRADE_LEVEL = 3

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
    personal_upgrade_level: int = 0  # 0..MAX_PERSONAL_UPGRADE_LEVEL, 코스메틱 전용
    team_upgrade_contributed: int = 0  # 이 참가자가 팀 업그레이드 풀에 낸 누적액(표시용)

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
            "personal_upgrade_level": self.personal_upgrade_level,
            "team_upgrade_contributed": self.team_upgrade_contributed,
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
        self.team_upgrade_pool: dict[str, int] = {}  # department -> 누적 투자액

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

    # -- 라운드 보상: 통과한 개인 + 우승 부서 소속 통과자 + 최종 당첨자 -----

    def award_round_rewards(
        self, passed_ids: set[str], winning_ids: set[str] | None = None
    ) -> dict[str, int]:
        """이미 fairness.py가 계산해둔 결과(통과자·우승 부서 소속)를 읽어서
        지급할 뿐, 추첨 계산 자체에는 관여하지 않는다. 카드가 없는(한 번도
        참여하지 않은) 참가자에게는 카드를 새로 만들지 않는다 -- 리더보드에
        아무 행동도 안 한 유령 항목이 뜨는 것을 막기 위함."""
        granted: dict[str, int] = {}
        for pid in passed_ids:
            card = self.cards.get(pid)
            if card is None:
                continue
            amount = FINISH_REWARD
            if winning_ids and pid in winning_ids:
                amount += TEAM_WIN_REWARD
            card.balance += amount
            granted[pid] = amount
        return granted

    def award_final_rewards(self, winner_ids: set[str]) -> dict[str, int]:
        granted: dict[str, int] = {}
        for pid in winner_ids:
            card = self.cards.get(pid)
            if card is None:
                continue
            card.balance += FINAL_WIN_REWARD
            granted[pid] = FINAL_WIN_REWARD
        return granted

    # -- 업그레이드 상점(순수 코스메틱) -------------------------------------

    def purchase_personal_upgrade(self, participant_id: str) -> BetCard:
        card = self.get_or_create_card(participant_id)
        if card.personal_upgrade_level >= MAX_PERSONAL_UPGRADE_LEVEL:
            raise GamblingError("이미 최대 레벨입니다")
        cost = PERSONAL_UPGRADE_COST[card.personal_upgrade_level]
        if cost > card.balance:
            raise GamblingError(f"보유 사이버머니({card.balance})로는 부족합니다 (필요 {cost})")
        card.balance -= cost
        card.personal_upgrade_level += 1
        return card

    def contribute_team_upgrade(self, participant_id: str, department: str, amount: int) -> BetCard:
        if amount <= 0:
            raise GamblingError("기여 금액은 0보다 커야 합니다")
        card = self.get_or_create_card(participant_id)
        if amount > card.balance:
            raise GamblingError(f"보유 사이버머니({card.balance})보다 많이 낼 수 없습니다")
        card.balance -= amount
        card.team_upgrade_contributed += amount
        self.team_upgrade_pool[department] = self.team_upgrade_pool.get(department, 0) + amount
        return card

    def team_upgrade_level(self, department: str) -> int:
        pool = self.team_upgrade_pool.get(department, 0)
        return min(MAX_TEAM_UPGRADE_LEVEL, pool // TEAM_UPGRADE_THRESHOLD)

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
            "team_upgrade_pool": dict(self.team_upgrade_pool),
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
            card.personal_upgrade_level = card_data.get("personal_upgrade_level", 0)
            card.team_upgrade_contributed = card_data.get("team_upgrade_contributed", 0)
            self.cards[pid] = card
        for r, s in data.get("round_state", {}).items():
            self.round_state[int(r)] = s
        for r, c in data.get("round_candidates", {}).items():
            self.round_candidates[int(r)] = c
        for r, p in data.get("round_pool", {}).items():
            self.round_pool[int(r)] = dict(p)
        for r, v in data.get("round_resolved", {}).items():
            self.round_resolved[int(r)] = v
        self.team_upgrade_pool = dict(data.get("team_upgrade_pool", {}))
