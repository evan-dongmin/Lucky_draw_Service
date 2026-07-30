import pytest

from app import fairness
from app import main as main_module
from app.director import RunbookSegment
from app.models import Session
from app.roster import generate_sample_participants

TINY_SEGMENTS = [
    RunbookSegment(phase="opening", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r1", duration_seconds=2.0, is_selection_window=False),
    RunbookSegment(phase="score_r1_select_r2", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r2", duration_seconds=0.02, is_selection_window=False),
    RunbookSegment(phase="score_r2_select_r3", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="race_r3", duration_seconds=0.02, is_selection_window=False),
    RunbookSegment(phase="final_announce", duration_seconds=0.01, is_selection_window=False),
    RunbookSegment(phase="verify", duration_seconds=0.01, is_selection_window=False),
]


def test_fast_forward_requires_racing_mode(client):
    sample = client.get("/api/roster/sample", params={"count": 10}).json()
    client.post(
        "/api/session",
        json={"participants": sample["participants"], "draw_count": 2, "mode": "roulette"},
    )
    resp = client.post("/api/racing/fast-forward")
    assert resp.status_code == 400


def test_fast_forward_requires_active_race(client):
    sample = client.get("/api/roster/sample", params={"count": 10}).json()
    client.post(
        "/api/session",
        json={"participants": sample["participants"], "draw_count": 2, "mode": "racing", "total_seconds": 150},
    )
    resp = client.post("/api/racing/fast-forward")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_fast_forward_ends_race_phase_early_with_correct_result(monkeypatch):
    """비상 조기 종료를 요청하면 race_r1이 원래 2초짜리 구간이었더라도
    거의 즉시 끝나고, 그럼에도 최종 통과자는 Fairness 결과와 정확히
    일치해야 한다(시간만 절약되고 결과 정합성은 그대로)."""
    participants = generate_sample_participants(30, seed=11)
    draw = fairness.compute_draw("ff-test", participants, draw_count=2, seed="ff-seed")
    session = Session(
        session_id="ff-test",
        participants=participants,
        draw_count=2,
        mode="racing",
        total_seconds=300.0,
        created_at="2026-01-01T00:00:00Z",
    )
    session.draws.append(draw)
    main_module.store.set_session(session)
    main_module.fast_forward_requests.clear()
    main_module.fast_forward_requests.add("ff-test")  # race_r1 진입 즉시 조기 종료되도록 미리 요청

    monkeypatch.setattr(main_module.director, "build_runbook", lambda **kwargs: list(TINY_SEGMENTS))
    monkeypatch.setattr(main_module, "RACE_TICK_INTERVAL_SECONDS", 0.05)

    messages = []

    async def fake_broadcast(message, sender=None, roles=None):
        messages.append(message)

    monkeypatch.setattr(main_module.hub, "broadcast", fake_broadcast)

    import time

    start = time.perf_counter()
    await main_module.run_racing_sequence("ff-test", 0, 300.0)
    elapsed = time.perf_counter() - start

    # 원래 race_r1만 2초인데, 조기 종료로 전체 시퀀스가 훨씬 빨리 끝나야 한다
    assert elapsed < 1.0, f"조기 종료가 적용되지 않음 (소요 {elapsed:.2f}초)"

    r1_ticks = [m for m in messages if m["type"] == "race_tick" and m["round"] == 1]
    assert len(r1_ticks) <= 2  # 즉시 ratio=1.0로 점프했으므로 틱이 거의 없어야 함
    assert r1_ticks[-1]["progress_ratio"] == pytest.approx(1.0)

    round_revealed = [m for m in messages if m["type"] == "round_revealed" and m["round"] == 1]
    assert round_revealed[0]["pass_ids"] == draw.round_pass_ids[1]

    assert "ff-test" not in main_module.fast_forward_requests  # 소비 후 플래그 해제
