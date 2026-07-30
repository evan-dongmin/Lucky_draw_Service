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
    # 기본 5명은 실제 테스터를 위해 봇 채움에서 제외된다
    assert body["bots_filled"] == 35

    session = client.get("/api/session").json()
    assert session["mode"] == "racing"
    assert session["predictions_enabled"] is True
    assert len(session["participants"]) == 40
    assert len(session["draws"]) == 1
    assert session["draws"][0]["commit"]

    lb = client.get("/api/predict/leaderboard?top_n=40").json()["top"]
    assert len(lb) == 35


def test_demo_start_leaves_reserved_slots_joinable_by_a_real_person(client):
    """R0 회귀 테스트: 데모 봇이 전원을 채워 실제 심사자가 /mobile에서
    아무도 선택할 수 없게 되는 문제가 재발하면 안 된다."""
    resp = client.post(
        "/api/demo/start",
        json={
            "participant_count": 40,
            "draw_count": 3,
            "total_seconds": 150,
            "with_bots": True,
            "reserved_for_human": 5,
        },
    )
    assert resp.status_code == 200

    session = client.get("/api/session").json()
    reserved_ids = [p["id"] for p in session["participants"][:5]]

    for pid in reserved_ids:
        join_resp = client.post("/api/predict/join", json={"participant_id": pid})
        assert join_resp.status_code == 200, f"예약된 참가자 {pid}가 참여할 수 없음"


def test_demo_start_without_bots_leaves_zero_participants(client):
    resp = client.post(
        "/api/demo/start",
        json={"participant_count": 20, "draw_count": 2, "total_seconds": 150, "with_bots": False},
    )
    assert resp.status_code == 200
    assert resp.json()["bots_filled"] == 0

    lb = client.get("/api/predict/leaderboard?top_n=20").json()["top"]
    assert lb == []
