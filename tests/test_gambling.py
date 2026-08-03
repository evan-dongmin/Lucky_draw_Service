import pytest

from app.gambling import ROUND_BONUS_CHIPS, STARTING_BALANCE, GamblingEngine, GamblingError


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
