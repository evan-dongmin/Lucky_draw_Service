def test_qrcode_endpoint_returns_png(client):
    resp = client.get("/api/qrcode")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 매직 바이트


def test_demo_start_creates_full_session_with_bots(client):
    resp = client.post(
        "/api/demo/start",
        json={"participant_count": 40, "draw_count": 3, "total_seconds": 150, "with_bots": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bots_filled"] == 40  # 아무도 미리 참여하지 않았으므로 전원 봇 채움

    session = client.get("/api/session").json()
    assert session["mode"] == "racing"
    assert session["predictions_enabled"] is True
    assert len(session["participants"]) == 40
    assert len(session["draws"]) == 1
    assert session["draws"][0]["commit"]

    lb = client.get("/api/predict/leaderboard?top_n=40").json()["top"]
    assert len(lb) == 40


def test_demo_start_without_bots_leaves_zero_participants(client):
    resp = client.post(
        "/api/demo/start",
        json={"participant_count": 20, "draw_count": 2, "total_seconds": 150, "with_bots": False},
    )
    assert resp.status_code == 200
    assert resp.json()["bots_filled"] == 0

    lb = client.get("/api/predict/leaderboard?top_n=20").json()["top"]
    assert lb == []
