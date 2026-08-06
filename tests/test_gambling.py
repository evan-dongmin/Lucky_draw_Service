import pytest

from app.gambling import (
    FINAL_WIN_REWARD,
    FINISH_REWARD,
    MAX_PERSONAL_UPGRADE_LEVEL,
    MAX_TEAM_UPGRADE_LEVEL,
    PERSONAL_UPGRADE_COST,
    ROUND_BONUS_CHIPS,
    STARTING_BALANCE,
    TEAM_RANK_REWARDS,
    TEAM_UPGRADE_THRESHOLD,
    GamblingEngine,
    GamblingError,
)


def test_new_card_starts_with_starting_balance():
    engine = GamblingEngine()
    card = engine.get_or_create_card("P1")
    assert card.balance == STARTING_BALANCE
    assert card.bets == {1: None, 2: None, 3: None}


def test_place_bet_rejected_before_window_opens():
    engine = GamblingEngine()
    with pytest.raises(GamblingError):
        engine.place_bet("P1", 1, "개발팀", 100)


def test_place_bet_rejected_for_unknown_target():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    with pytest.raises(GamblingError):
        engine.place_bet("P1", 1, "없는팀", 50)


def test_place_bet_rejected_when_amount_exceeds_balance():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀"])
    with pytest.raises(GamblingError):
        engine.place_bet("P1", 1, "개발팀", STARTING_BALANCE + 1)


def test_place_bet_rejected_for_negative_amount():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀"])
    with pytest.raises(GamblingError):
        engine.place_bet("P1", 1, "개발팀", -10)


def test_place_bet_deducts_balance_and_fills_pool():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    card = engine.place_bet("P1", 1, "개발팀", 100)
    assert card.balance == STARTING_BALANCE - 100
    assert card.bets[1] == ("개발팀", 100)
    assert engine.round_pool[1]["개발팀"] == 100
    assert engine.round_pool[1]["영업팀"] == 0


def test_rebet_within_same_round_refunds_previous_stake():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.place_bet("P1", 1, "개발팀", 100)
    card = engine.place_bet("P1", 1, "영업팀", 60)

    assert card.balance == STARTING_BALANCE - 60
    assert card.bets[1] == ("영업팀", 60)
    assert engine.round_pool[1]["개발팀"] == 0
    assert engine.round_pool[1]["영업팀"] == 60


def test_rebet_with_zero_amount_withdraws_bet():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀"])
    engine.place_bet("P1", 1, "개발팀", 100)
    card = engine.place_bet("P1", 1, "개발팀", 0)

    assert card.balance == STARTING_BALANCE
    assert card.bets[1] is None
    assert engine.round_pool[1]["개발팀"] == 0


def test_cannot_bet_after_lock():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀"])
    engine.place_bet("P1", 1, "개발팀", 50)
    engine.lock_round(1)
    with pytest.raises(GamblingError):
        engine.place_bet("P1", 1, "개발팀", 10)


def test_lock_round_does_not_force_bets_on_unparticipating_cards():
    """확신도 배분과 달리 자동 베팅은 없다 -- 베팅하지 않은 참가자는
    그냥 구경만 하고, 잔액은 그대로여야 한다."""
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.get_or_create_card("P1")  # 베팅하지 않음
    engine.lock_round(1)

    card = engine.cards["P1"]
    assert card.bets[1] is None
    assert card.balance == STARTING_BALANCE
    assert card.locked[1] is True


def test_round_bonus_chips_granted_on_round_2_and_3_open_but_not_round_1():
    engine = GamblingEngine()
    card = engine.get_or_create_card("P1")
    balance_before = card.balance

    engine.open_round(1, ["A"])
    assert card.balance == balance_before  # 1라운드는 보너스 없음

    engine.open_round(2, ["A"])
    assert card.balance == balance_before + ROUND_BONUS_CHIPS

    engine.open_round(3, ["A"])
    assert card.balance == balance_before + ROUND_BONUS_CHIPS * 2


def test_pari_mutuel_payout_splits_pool_proportionally_to_winners():
    engine = GamblingEngine()
    engine.open_round(1, ["개발팀", "영업팀"])
    engine.place_bet("P1", 1, "개발팀", 100)  # 이긴다
    engine.place_bet("P2", 1, "개발팀", 300)  # 이긴다 (P1의 3배)
    engine.place_bet("P3", 1, "영업팀", 200)  # 진다
    engine.lock_round(1)

    payload = engine.resolve_round(1, hit_set={"개발팀"})

    # 전체 판돈 600을 승자 판돈(400) 비율로 나눈다: P1 100/400*600=150, P2 300/400*600=450
    assert engine.cards["P1"].balance == STARTING_BALANCE - 100 + 150
    assert engine.cards["P2"].balance == STARTING_BALANCE - 300 + 450
    # 패자는 건 돈을 그대로 잃는다(추가 손실 없음 -- 마이너스 잔액 불가)
    assert engine.cards["P3"].balance == STARTING_BALANCE - 200
    assert engine.cards["P3"].balance >= 0

    assert payload["total_pool"] == 600
    assert payload["pool"]["개발팀"] == 400


def test_balance_never_goes_negative_even_on_full_loss():
    engine = GamblingEngine()
    engine.open_round(1, ["A", "B"])
    engine.place_bet("P1", 1, "A", STARTING_BALANCE)  # 전액 베팅
    engine.lock_round(1)
    engine.resolve_round(1, hit_set={"B"})  # 완전히 틀림

    assert engine.cards["P1"].balance == 0
    assert engine.cards["P1"].net[1] == -STARTING_BALANCE


def test_nobody_bet_on_winning_target_pool_simply_vanishes():
    """아무도 정답을 맞히지 못하면(패리뮤추얼 특성상) 판돈은 그대로 소멸한다
    -- 지급할 승자가 없으므로 운영자가 추가로 지급할 것도 없다."""
    engine = GamblingEngine()
    engine.open_round(1, ["A", "B"])
    engine.place_bet("P1", 1, "A", 100)
    engine.place_bet("P2", 1, "B", 50)
    engine.lock_round(1)
    engine.resolve_round(1, hit_set={"C"})  # 후보 밖의 결과(단순화를 위한 극단값)

    assert engine.cards["P1"].balance == STARTING_BALANCE - 100
    assert engine.cards["P2"].balance == STARTING_BALANCE - 50


def test_resolve_round_is_idempotent_recomputation():
    """서버 재시작 후 재계산 시나리오: 같은 hit_set으로 다시 정산해도
    중복 지급/차감이 없어야 한다."""
    engine = GamblingEngine()
    engine.open_round(1, ["A", "B"])
    engine.place_bet("P1", 1, "A", 100)
    engine.place_bet("P2", 1, "B", 100)
    engine.lock_round(1)

    engine.resolve_round(1, hit_set={"A"})
    balance_after_first = engine.cards["P1"].balance

    engine.resolve_round(1, hit_set={"A"})  # 재계산
    balance_after_second = engine.cards["P1"].balance

    assert balance_after_first == balance_after_second


def test_live_odds_reflects_current_pool_before_lock():
    engine = GamblingEngine()
    engine.open_round(1, ["A", "B"])
    engine.place_bet("P1", 1, "A", 100)
    engine.place_bet("P2", 1, "B", 300)

    odds = engine.live_odds(1)
    assert odds["total_pool"] == 400
    assert odds["odds"]["A"] == pytest.approx(4.0)  # 400/100
    assert odds["odds"]["B"] == pytest.approx(round(400 / 300, 2))


def test_live_odds_none_for_target_with_no_bets():
    engine = GamblingEngine()
    engine.open_round(1, ["A", "B"])
    engine.place_bet("P1", 1, "A", 50)
    odds = engine.live_odds(1)
    assert odds["odds"]["B"] is None  # 0으로 나누지 않고 None


def test_leaderboard_sorted_descending_by_balance_with_stable_tiebreak():
    engine = GamblingEngine()
    engine.get_or_create_card("P3")
    engine.get_or_create_card("P1")
    engine.get_or_create_card("P2")

    top = engine.leaderboard(top_n=10)
    balances = {c.participant_id: c.balance for c in top}
    assert len(set(balances.values())) == 1  # 전원 동일 시작 잔액
    assert [c.participant_id for c in top] == ["P1", "P2", "P3"]  # 동점자는 id 오름차순


def test_engine_to_dict_load_dict_round_trip_preserves_full_state():
    engine = GamblingEngine()
    engine.open_round(1, ["A", "B"])
    engine.place_bet("P1", 1, "A", 120)
    engine.lock_round(1)
    engine.resolve_round(1, hit_set={"A"})
    engine.open_round(2, ["X", "Y"])

    snapshot = engine.to_dict()
    restored = GamblingEngine()
    restored.load_dict(snapshot)

    assert restored.cards["P1"].to_dict() == engine.cards["P1"].to_dict()
    assert restored.round_state == engine.round_state
    assert restored.round_candidates == engine.round_candidates
    assert restored.round_pool == engine.round_pool
    assert restored.round_resolved == engine.round_resolved


def test_load_dict_restores_in_place_keeping_same_instance():
    """모듈 싱글턴 패턴에서 다른 코드가 들고 있는 참조가 깨지지 않아야 한다."""
    engine = GamblingEngine()
    engine.open_round(1, ["A"])
    engine.place_bet("P1", 1, "A", 50)
    reference = engine

    snapshot = engine.to_dict()
    engine.reset()
    assert "P1" not in engine.cards

    engine.load_dict(snapshot)
    assert reference is engine
    assert "P1" in reference.cards
    assert reference.cards["P1"].balance == STARTING_BALANCE - 50


# ---------------------------------------------------------------------------
# 라운드 보상: 통과한 개인 + 부서 순위별 차등 보상 + 최종 당첨자
# ---------------------------------------------------------------------------


def test_award_round_rewards_grants_finish_bonus_only_to_existing_cards():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")
    engine.get_or_create_card("P2")
    # P3는 카드가 없다 -- 한 번도 참여하지 않은 참가자(유령 리더보드 항목 방지)

    granted = engine.award_round_rewards(passed_ids={"P1", "P2", "P3"})

    assert granted == {"P1": FINISH_REWARD, "P2": FINISH_REWARD}
    assert engine.cards["P1"].balance == STARTING_BALANCE + FINISH_REWARD
    assert "P3" not in engine.cards


def test_award_round_rewards_grants_tiered_team_bonus_by_rank():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")  # 1위 부서 소속 통과자
    engine.get_or_create_card("P2")  # 2위 부서 소속 통과자
    engine.get_or_create_card("P3")  # 3위 부서 소속 통과자
    engine.get_or_create_card("P4")  # 4위 부서 소속 통과자(순위표 밖 -- 추가 보상 없음)

    granted = engine.award_round_rewards(
        passed_ids={"P1", "P2", "P3", "P4"},
        ranked_dept_ids=[{"P1"}, {"P2"}, {"P3"}, {"P4"}],
    )

    assert granted["P1"] == FINISH_REWARD + TEAM_RANK_REWARDS[0]
    assert granted["P2"] == FINISH_REWARD + TEAM_RANK_REWARDS[1]
    assert granted["P3"] == FINISH_REWARD + TEAM_RANK_REWARDS[2]
    assert granted["P4"] == FINISH_REWARD
    assert engine.cards["P1"].balance == STARTING_BALANCE + FINISH_REWARD + TEAM_RANK_REWARDS[0]


def test_team_rank_bonus_not_granted_to_ranked_team_member_who_did_not_pass():
    """순위권 부서 소속이라도 그 라운드를 통과하지 못했으면(탈락) 보상 대상이 아니다."""
    engine = GamblingEngine()
    engine.get_or_create_card("P1")

    granted = engine.award_round_rewards(passed_ids=set(), ranked_dept_ids=[{"P1"}])

    assert granted == {}
    assert engine.cards["P1"].balance == STARTING_BALANCE


def test_award_final_rewards_grants_only_to_winners_with_existing_cards():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")

    granted = engine.award_final_rewards(winner_ids={"P1", "P2"})

    assert granted == {"P1": FINAL_WIN_REWARD}
    assert engine.cards["P1"].balance == STARTING_BALANCE + FINAL_WIN_REWARD


# ---------------------------------------------------------------------------
# 업그레이드 상점(순수 코스메틱)
# ---------------------------------------------------------------------------


def test_purchase_personal_upgrade_deducts_cost_and_increments_level():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")

    card = engine.purchase_personal_upgrade("P1")

    assert card.personal_upgrade_level == 1
    assert card.balance == STARTING_BALANCE - PERSONAL_UPGRADE_COST[0]


def test_purchase_personal_upgrade_cost_increases_per_level():
    engine = GamblingEngine()
    card = engine.get_or_create_card("P1")
    card.balance = 10_000

    engine.purchase_personal_upgrade("P1")
    balance_after_first = engine.cards["P1"].balance
    engine.purchase_personal_upgrade("P1")

    spent_second = balance_after_first - engine.cards["P1"].balance
    assert spent_second == PERSONAL_UPGRADE_COST[1]
    assert spent_second > PERSONAL_UPGRADE_COST[0]


def test_purchase_personal_upgrade_rejects_insufficient_balance():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")  # 시작 잔액 500 < 첫 레벨 비용 아님 -> 충분

    card = engine.get_or_create_card("P1")
    card.balance = 10
    with pytest.raises(GamblingError):
        engine.purchase_personal_upgrade("P1")


def test_purchase_personal_upgrade_rejects_beyond_max_level():
    engine = GamblingEngine()
    card = engine.get_or_create_card("P1")
    card.balance = 100_000
    for _ in range(MAX_PERSONAL_UPGRADE_LEVEL):
        engine.purchase_personal_upgrade("P1")
    assert engine.cards["P1"].personal_upgrade_level == MAX_PERSONAL_UPGRADE_LEVEL

    with pytest.raises(GamblingError):
        engine.purchase_personal_upgrade("P1")


def test_contribute_team_upgrade_pools_across_multiple_participants():
    engine = GamblingEngine()
    for pid in ["P1", "P2"]:
        card = engine.get_or_create_card(pid)
        card.balance = 1000

    engine.contribute_team_upgrade("P1", "개발팀", 300)
    engine.contribute_team_upgrade("P2", "개발팀", 300)

    assert engine.team_upgrade_pool["개발팀"] == 600
    assert engine.cards["P1"].balance == 700
    assert engine.cards["P1"].team_upgrade_contributed == 300
    assert engine.team_upgrade_level("개발팀") == 600 // TEAM_UPGRADE_THRESHOLD


def test_team_upgrade_level_caps_at_max():
    engine = GamblingEngine()
    card = engine.get_or_create_card("P1")
    card.balance = 100_000
    engine.contribute_team_upgrade("P1", "개발팀", TEAM_UPGRADE_THRESHOLD * (MAX_TEAM_UPGRADE_LEVEL + 5))

    assert engine.team_upgrade_level("개발팀") == MAX_TEAM_UPGRADE_LEVEL


def test_contribute_team_upgrade_rejects_amount_over_balance():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")
    with pytest.raises(GamblingError):
        engine.contribute_team_upgrade("P1", "개발팀", STARTING_BALANCE + 1)


def test_contribute_team_upgrade_rejects_non_positive_amount():
    engine = GamblingEngine()
    engine.get_or_create_card("P1")
    with pytest.raises(GamblingError):
        engine.contribute_team_upgrade("P1", "개발팀", 0)


def test_upgrade_state_round_trips_through_snapshot():
    engine = GamblingEngine()
    card = engine.get_or_create_card("P1")
    card.balance = 10_000
    engine.purchase_personal_upgrade("P1")
    engine.contribute_team_upgrade("P1", "개발팀", 300)

    snapshot = engine.to_dict()
    restored = GamblingEngine()
    restored.load_dict(snapshot)

    assert restored.cards["P1"].personal_upgrade_level == 1
    assert restored.cards["P1"].team_upgrade_contributed == 300
    assert restored.team_upgrade_pool == {"개발팀": 300}
