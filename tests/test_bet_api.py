from app.gambling import STARTING_BALANCE


def _create_gambling_session(client, count=30, draw_count=3, total_seconds=300.0):
    sample = client.get("/api/roster/sample", params={"count": count}).json()
    resp = client.post(
        "/api/session",
        json={
            "participants": sample["participants"],
            "draw_count": draw_count,
            "mode": "racing",
            "total_seconds": total_seconds,
            "predictions_enabled": True,
            "prediction_mode": "gambling",
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_create_session_rejects_unknown_prediction_mode(client):
    sample = client.get("/api/roster/sample", params={"count": 10}).json()
    resp = client.post(
        "/api/session",
        json={
            "participants": sample["participants"],
            "draw_count": 2,
            "mode": "racing",
            "predictions_enabled": True,
            "prediction_mode": "not-a-real-mode",
        },
    )
    assert resp.status_code == 400


def test_join_in_gambling_session_returns_bet_card_shape(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    resp = client.post("/api/predict/join", json={"participant_id": pid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "gambling"
    assert body["card"]["balance"] == STARTING_BALANCE
    assert body["card"]["bets"] == {"1": None, "2": None, "3": None}


def test_predict_allocate_and_choose_rejected_in_gambling_mode(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp = client.post("/api/predict/allocate", json={"token": token, "alloc": {1: 34, 2: 33, 3: 33}})
    assert resp.status_code == 400

    resp2 = client.post("/api/predict/choose", json={"token": token, "round": 1, "target": "아무팀"})
    assert resp2.status_code == 400


def test_bet_place_rejected_in_confidence_mode(client):
    sample = client.get("/api/roster/sample", params={"count": 10}).json()
    resp = client.post(
        "/api/session",
        json={
            "participants": sample["participants"],
            "draw_count": 2,
            "mode": "racing",
            "predictions_enabled": True,
            "prediction_mode": "confidence",
        },
    )
    assert resp.status_code == 200
    pid = resp.json()["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp2 = client.post("/api/bet/place", json={"token": token, "round": 1, "target": "아무팀", "amount": 10})
    assert resp2.status_code == 400


def test_bet_place_requires_open_window(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]
    # 커밋 전이라 R1 베팅 창이 열려 있지 않음
    resp = client.post("/api/bet/place", json={"token": token, "round": 1, "target": "아무팀", "amount": 10})
    assert resp.status_code == 400


def test_bet_place_succeeds_after_commit_and_updates_balance(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    client.post("/api/draw/commit")
    me = client.get("/api/predict/me", params={"token": token}).json()
    assert me["mode"] == "gambling"
    assert me["round_state"]["1"] == "open"
    candidates = me["round_candidates"]["1"]
    assert candidates

    resp = client.post(
        "/api/bet/place", json={"token": token, "round": 1, "target": candidates[0], "amount": 120}
    )
    assert resp.status_code == 200
    card = resp.json()
    assert card["balance"] == STARTING_BALANCE - 120
    assert card["bets"]["1"] == {"target": candidates[0], "amount": 120}


def test_bet_place_rejects_amount_over_balance(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]
    client.post("/api/draw/commit")
    candidates = client.get("/api/predict/me", params={"token": token}).json()["round_candidates"]["1"]

    resp = client.post(
        "/api/bet/place",
        json={"token": token, "round": 1, "target": candidates[0], "amount": STARTING_BALANCE + 1},
    )
    assert resp.status_code == 400


def test_predict_me_exposes_live_odds_for_open_gambling_round(client):
    session = _create_gambling_session(client)
    pids = [p["id"] for p in session["participants"][:2]]
    tokens = [client.post("/api/predict/join", json={"participant_id": pid}).json()["token"] for pid in pids]

    client.post("/api/draw/commit")
    candidates = client.get("/api/predict/me", params={"token": tokens[0]}).json()["round_candidates"]["1"]

    client.post("/api/bet/place", json={"token": tokens[0], "round": 1, "target": candidates[0], "amount": 100})
    client.post("/api/bet/place", json={"token": tokens[1], "round": 1, "target": candidates[0], "amount": 300})

    me = client.get("/api/predict/me", params={"token": tokens[0]}).json()
    live = me["live"]["1"]
    assert live["total_pool"] == 400
    assert live["pool"][candidates[0]] == 400


def test_predict_live_endpoint_is_public_and_requires_no_token(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]
    client.post("/api/draw/commit")
    candidates = client.get("/api/predict/me", params={"token": token}).json()["round_candidates"]["1"]
    client.post("/api/bet/place", json={"token": token, "round": 1, "target": candidates[0], "amount": 50})

    resp = client.get("/api/predict/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "gambling"
    assert body["rounds"]["1"]["pool"][candidates[0]] == 50


def test_bet_leaderboard_endpoint_returns_balances(client):
    session = _create_gambling_session(client)
    pid = session["participants"][0]["id"]
    client.post("/api/predict/join", json={"participant_id": pid})
    resp = client.get("/api/bet/leaderboard")
    assert resp.status_code == 200
    top = resp.json()["top"]
    assert any(entry["participant_id"] == pid and entry["balance"] == STARTING_BALANCE for entry in top)


def test_bots_fill_places_bets_in_gambling_mode(client):
    _create_gambling_session(client, count=15)
    client.post("/api/draw/commit")
    resp = client.post("/api/predict/bots/fill")
    assert resp.status_code == 200
    assert resp.json()["filled"] == 15

    lb = client.get("/api/bet/leaderboard?top_n=15").json()["top"]
    assert len(lb) == 15
    # 봇들은 R1이 열려 있는 동안 채워지므로 최소 한 명은 잔액이 시작값과 달라야 한다
    assert any(entry["balance"] != STARTING_BALANCE for entry in lb)
