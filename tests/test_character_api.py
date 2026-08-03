from app.characters import CHARACTER_ROSTER


def _create_racing_session(client, count=20, predictions_enabled=False, prediction_mode="confidence"):
    sample = client.get("/api/roster/sample", params={"count": count}).json()
    resp = client.post(
        "/api/session",
        json={
            "participants": sample["participants"],
            "draw_count": 2,
            "mode": "racing",
            "total_seconds": 300.0,
            "predictions_enabled": predictions_enabled,
            "prediction_mode": prediction_mode,
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_character_roster_endpoint_returns_fixed_list(client):
    resp = client.get("/api/character/roster")
    assert resp.status_code == 200
    roster = resp.json()["roster"]
    assert roster == CHARACTER_ROSTER
    assert len(roster) == 8
    assert len({c["id"] for c in roster}) == 8  # id 중복 없음


def test_join_works_without_predictions_enabled_for_character_only_flow(client):
    """예측/베팅 게임이 꺼진 순수 레이싱 세션에서도 캐릭터 선택을 위해
    참여(join)는 가능해야 한다."""
    session = _create_racing_session(client, predictions_enabled=False)
    pid = session["participants"][0]["id"]
    resp = client.post("/api/predict/join", json={"participant_id": pid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["predictions_enabled"] is False
    assert body["card"] is None


def test_join_rejected_for_non_racing_session(client):
    sample = client.get("/api/roster/sample", params={"count": 10}).json()
    client.post(
        "/api/session",
        json={"participants": sample["participants"], "draw_count": 2, "mode": "roulette"},
    )
    resp = client.post("/api/predict/join", json={"participant_id": sample["participants"][0]["id"]})
    assert resp.status_code == 400


def test_predict_me_reports_predictions_disabled_without_creating_card(client):
    session = _create_racing_session(client, predictions_enabled=False)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp = client.get("/api/predict/me", params={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["predictions_enabled"] is False
    assert body["card"] is None
    assert body["participant_id"] == pid


def test_character_choose_and_me_roundtrip(client):
    session = _create_racing_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp = client.post("/api/character/choose", json={"token": token, "character_id": "nitro"})
    assert resp.status_code == 200
    assert resp.json() == {"participant_id": pid, "character_id": "nitro"}

    me = client.get("/api/character/me", params={"token": token}).json()
    assert me == {"participant_id": pid, "character_id": "nitro"}


def test_character_choose_rejects_unknown_id(client):
    session = _create_racing_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp = client.post("/api/character/choose", json={"token": token, "character_id": "not-a-character"})
    assert resp.status_code == 400


def test_character_choose_rejects_invalid_token(client):
    resp = client.post(
        "/api/character/choose", json={"token": "not-a-real-token", "character_id": "nitro"}
    )
    assert resp.status_code == 401


def test_character_can_be_changed(client):
    session = _create_racing_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    client.post("/api/character/choose", json={"token": token, "character_id": "nitro"})
    resp = client.post("/api/character/choose", json={"token": token, "character_id": "shield"})
    assert resp.status_code == 200

    me = client.get("/api/character/me", params={"token": token}).json()
    assert me["character_id"] == "shield"


def test_character_choices_endpoint_is_public_and_aggregates_all(client):
    session = _create_racing_session(client, count=5)
    tokens = []
    for p in session["participants"][:3]:
        token = client.post("/api/predict/join", json={"participant_id": p["id"]}).json()["token"]
        tokens.append((p["id"], token))

    for pid, token in tokens:
        client.post("/api/character/choose", json={"token": token, "character_id": "rocket"})

    resp = client.get("/api/character/choices")
    assert resp.status_code == 200
    choices = resp.json()["choices"]
    for pid, _ in tokens:
        assert choices[pid] == "rocket"


def test_character_me_without_choice_returns_none(client):
    session = _create_racing_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    me = client.get("/api/character/me", params={"token": token}).json()
    assert me["character_id"] is None


def test_session_reset_clears_character_choices(client):
    session = _create_racing_session(client)
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]
    client.post("/api/character/choose", json={"token": token, "character_id": "nitro"})

    client.post("/api/session/reset")

    resp = client.get("/api/character/choices")
    assert resp.json()["choices"] == {}


def test_character_selection_coexists_with_gambling_mode(client):
    """캐릭터 선택은 예측 모드와 무관하게 동작해야 한다(갬블링 모드에서도)."""
    session = _create_racing_session(client, predictions_enabled=True, prediction_mode="gambling")
    pid = session["participants"][0]["id"]
    token = client.post("/api/predict/join", json={"participant_id": pid}).json()["token"]

    resp = client.post("/api/character/choose", json={"token": token, "character_id": "wave"})
    assert resp.status_code == 200

    me = client.get("/api/predict/me", params={"token": token}).json()
    assert me["mode"] == "gambling"
    assert me["card"]["balance"] is not None

    char_me = client.get("/api/character/me", params={"token": token}).json()
    assert char_me["character_id"] == "wave"
