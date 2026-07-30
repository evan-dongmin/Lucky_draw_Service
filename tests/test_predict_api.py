def _create_prediction_session(client, count=30, draw_count=3, total_seconds=300.0):
    sample = client.get("/api/roster/sample", params={"count": count}).json()
    resp = client.post(
        "/api/session",
        json={
            "participants": sample["participants"],
            "draw_count": draw_count,
            "mode": "racing",
            "total_seconds": total_seconds,
            "predictions_enabled": True,
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_departments_endpoint_requires_predictions_enabled(client):
    sample = client.get("/api/roster/sample", params={"count": 10}).json()
    client.post(
        "/api/session",
        json={"participants": sample["participants"], "draw_count": 2, "mode": "roulette"},
    )
    resp = client.get("/api/predict/departments")
    assert resp.status_code == 400


def test_departments_endpoint_lists_groups(client):
    _create_prediction_session(client)
    resp = client.get("/api/predict/departments")
    assert resp.status_code == 200
    groups = resp.json()
    assert len(groups) >= 1
    total_people = sum(len(members) for members in groups.values())
    assert total_people == 30


def test_join_creates_token_and_default_card(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    resp = client.post("/api/predict/join", json={"participant_id": pid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["participant_id"] == pid
    assert body["token"]
    assert sum(body["card"]["alloc"].values()) == 100


def test_join_rejects_unknown_participant(client):
    _create_prediction_session(client)
    resp = client.post("/api/predict/join", json={"participant_id": "does-not-exist"})
    assert resp.status_code == 404


def test_join_rejects_duplicate_from_new_token(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    client.post("/api/predict/join", json={"participant_id": pid})
    resp = client.post("/api/predict/join", json={"participant_id": pid})
    assert resp.status_code == 409


def test_join_with_existing_token_restores_session(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    first = client.post("/api/predict/join", json={"participant_id": pid}).json()
    second = client.post(
        "/api/predict/join", json={"participant_id": pid, "existing_token": first["token"]}
    ).json()
    assert second["token"] == first["token"]
    assert second["participant_id"] == pid


def test_allocate_and_me_roundtrip(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp = client.post("/api/predict/allocate", json={"token": token, "alloc": {1: 20, 2: 30, 3: 50}})
    assert resp.status_code == 200
    assert resp.json()["alloc"] == {"1": 20, "2": 30, "3": 50}

    me = client.get("/api/predict/me", params={"token": token}).json()
    assert me["card"]["alloc"] == {"1": 20, "2": 30, "3": 50}


def test_allocate_rejects_invalid_total(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]
    resp = client.post("/api/predict/allocate", json={"token": token, "alloc": {1: 50, 2: 40, 3: 40}})
    assert resp.status_code == 400


def test_choose_requires_open_window(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]
    # 커밋 전이라 R1 선택창이 열려 있지 않음
    resp = client.post("/api/predict/choose", json={"token": token, "round": 1, "target": "아무부서"})
    assert resp.status_code == 400


def test_choose_succeeds_after_commit_opens_r1_window(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    client.post("/api/draw/commit")
    me = client.get("/api/predict/me", params={"token": token}).json()
    assert me["round_state"]["1"] == "open"
    candidates = me["round_candidates"]["1"]
    assert candidates

    resp = client.post("/api/predict/choose", json={"token": token, "round": 1, "target": candidates[0]})
    assert resp.status_code == 200
    assert resp.json()["target"]["1"] == candidates[0]


def test_leaderboard_endpoint_returns_names(client):
    session = _create_prediction_session(client)
    pid = session["participants"][0]["id"]
    client.post("/api/predict/join", json={"participant_id": pid})
    resp = client.get("/api/predict/leaderboard")
    assert resp.status_code == 200
    top = resp.json()["top"]
    assert any(entry["participant_id"] == pid for entry in top)
    assert all("name" in entry for entry in top)


def test_invalid_token_rejected(client):
    _create_prediction_session(client)
    resp = client.get("/api/predict/me", params={"token": "not-a-real-token"})
    assert resp.status_code == 401
