def _create_sample_session(client, count=50, draw_count=3):
    sample = client.get("/api/roster/sample", params={"count": count}).json()
    resp = client.post(
        "/api/session",
        json={"participants": sample["participants"], "draw_count": draw_count, "mode": "roulette"},
    )
    assert resp.status_code == 200
    return resp.json()


def test_no_session_returns_404(client):
    resp = client.get("/api/session")
    assert resp.status_code == 404


def test_roster_preview_text(client):
    resp = client.post(
        "/api/roster/preview",
        json={"text": "사번,이름,팀\nP1,홍길동,개발팀\nP2,김철수,영업팀\n"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["participants"][0]["name"] == "홍길동"


def test_roster_preview_invalid_text_returns_400(client):
    resp = client.post(
        "/api/roster/preview",
        json={"text": "id,name,team\nP1,홍길동,개발팀\nP1,중복,영업팀\n"},
    )
    assert resp.status_code == 400


def test_create_session_and_get(client):
    session = _create_sample_session(client)
    assert session["draw_count"] == 3
    assert len(session["participants"]) == 50

    fetched = client.get("/api/session").json()
    assert fetched["session_id"] == session["session_id"]


def test_commit_hides_seed_and_winners_until_reveal(client):
    _create_sample_session(client)
    commit_resp = client.post("/api/draw/commit")
    assert commit_resp.status_code == 200
    draw = commit_resp.json()

    assert draw["seed"] is None
    assert draw["winners"] == []
    assert draw["ranking"] == []
    assert draw["commit"]
    # 스냅샷(명단·부서 편성)은 리빌 전에도 공개되어야 한다 (온보딩·부서 소개용)
    assert draw["snapshot"]["participants"]
    assert draw["snapshot"]["departments"]


def test_reveal_exposes_winners_matching_commit(client):
    _create_sample_session(client, draw_count=3)
    commit_resp = client.post("/api/draw/commit").json()

    reveal_resp = client.post("/api/draw/reveal", json={})
    assert reveal_resp.status_code == 200
    revealed = reveal_resp.json()

    assert revealed["seed"]
    assert len(revealed["winners"]) == 3
    assert revealed["commit"] == commit_resp["commit"]


def test_verify_endpoint_confirms_recompute_matches(client):
    _create_sample_session(client, draw_count=2)
    client.post("/api/draw/commit")
    client.post("/api/draw/reveal", json={})

    verify_resp = client.get("/api/verify/0")
    assert verify_resp.status_code == 200
    body = verify_resp.json()
    assert body["matches"] is True
    assert body["server_recomputed_commit"] == body["declared_commit"]
    assert body["server_recomputed_winners"] == body["declared_winners"]


def test_verify_before_reveal_returns_409(client):
    _create_sample_session(client)
    client.post("/api/draw/commit")
    resp = client.get("/api/verify/0")
    assert resp.status_code == 409


def test_redraw_with_exclusion_removes_previous_winners_from_pool(client):
    _create_sample_session(client, count=20, draw_count=2)
    client.post("/api/draw/commit")
    reveal1 = client.post("/api/draw/reveal", json={}).json()
    first_winners = set(reveal1["winners"])

    redraw_resp = client.post("/api/draw/redraw", json={"exclude_previous_winners": True})
    assert redraw_resp.status_code == 200

    reveal2 = client.post("/api/draw/reveal", json={"draw_index": 1}).json()
    second_winners = set(reveal2["winners"])

    assert first_winners.isdisjoint(second_winners)

    session = client.get("/api/session").json()
    assert set(session["excluded_ids"]) == first_winners


def test_redraw_without_reveal_of_previous_rejected(client):
    _create_sample_session(client, count=20, draw_count=2)
    client.post("/api/draw/commit")
    resp = client.post("/api/draw/redraw", json={"exclude_previous_winners": True})
    assert resp.status_code == 400


def test_reset_clears_session(client):
    _create_sample_session(client)
    resp = client.post("/api/session/reset")
    assert resp.status_code == 200
    assert client.get("/api/session").status_code == 404


def test_exclude_endpoint_updates_session(client):
    session = _create_sample_session(client, count=10)
    excluded = [session["participants"][0]["id"]]
    resp = client.post("/api/session/excluded", json={"excluded_ids": excluded})
    assert resp.status_code == 200
    assert resp.json()["excluded_ids"] == excluded
