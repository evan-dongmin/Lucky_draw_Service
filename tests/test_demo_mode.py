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

    # 리더보드에는 봇뿐 아니라 명단 전원이 올라온다 -- 커밋 시점에
    # enroll_all이 전원에게 카드를 만들어 두기 때문(모바일로 참여하지
    # 않아도 경품 대상에 남는다는 설계).
    lb = client.get("/api/predict/leaderboard?top_n=40").json()["top"]
    assert len(lb) == 40


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


def test_demo_start_without_bots_still_enrolls_everyone_at_zero(client):
    """봇을 채우지 않아도 명단 전원이 0점으로 리더보드에 올라와 있어야
    한다 -- 아무도 폰을 안 들어도 경품 대상은 명단 전체다."""
    resp = client.post(
        "/api/demo/start",
        json={"participant_count": 20, "draw_count": 2, "total_seconds": 150, "with_bots": False},
    )
    assert resp.status_code == 200
    assert resp.json()["bots_filled"] == 0

    lb = client.get("/api/predict/leaderboard?top_n=20").json()["top"]
    assert len(lb) == 20
    assert all(entry["score"] == 0 for entry in lb)


def test_demo_start_rejects_total_seconds_below_director_floor_without_clearing_session(client):
    """데모는 예측 게임을 항상 켠 채로 시작하므로 하한은 150초다. 과거에는
    이 검증이 없어 레이스가 백그라운드에서 조용히 실패해 무대가 커밋 화면에서
    멈춘 것처럼 보였다. 지금은 즉시 400을 돌려주고, 검증 실패 시점이 세션을
    지우기 전이어야 하므로 기존 세션이 그대로 남아 있어야 한다."""
    first = client.post(
        "/api/demo/start",
        json={"participant_count": 20, "draw_count": 2, "total_seconds": 150},
    )
    assert first.status_code == 200
    existing_session_id = client.get("/api/session").json()["session_id"]

    resp = client.post(
        "/api/demo/start",
        json={"participant_count": 20, "draw_count": 2, "total_seconds": 10},
    )
    assert resp.status_code == 400

    session = client.get("/api/session").json()
    assert session["session_id"] == existing_session_id
