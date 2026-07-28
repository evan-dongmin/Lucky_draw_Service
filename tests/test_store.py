from app.models import DrawResult, Participant, RoundPlan, Session
from app.store import SessionStore


def _make_session() -> Session:
    return Session(
        session_id="sess-1",
        participants=[
            Participant(id="p1", name="김철수", team="A팀"),
            Participant(id="p2", name="이영희", team="B팀"),
        ],
        draw_count=1,
        excluded_ids=["p3"],
        mode="race",
        created_at="2026-07-29T09:00:00",
        draws=[
            DrawResult(
                seed="deadbeef",
                commit="abc123",
                winners=["p2"],
                ranking=["p2", "p1"],
                round_pass_ids={1: ["p2", "p1"], 2: ["p2"]},
                revealed=True,
                created_at="2026-07-29T09:01:00",
                revealed_at="2026-07-29T09:05:00",
            )
        ],
        round_plans=[RoundPlan(round_index=1, pass_count=2, duration_seconds=90.0)],
    )


def test_snapshot_round_trip(tmp_path):
    snapshot_path = tmp_path / "session_snapshot.json"
    store = SessionStore(snapshot_path=snapshot_path)
    session = _make_session()
    store.set_session(session)

    assert snapshot_path.exists()

    restarted_store = SessionStore(snapshot_path=snapshot_path)
    restored = restarted_store.load_snapshot()

    assert restored is not None
    assert restored.session_id == session.session_id
    assert [p.id for p in restored.participants] == ["p1", "p2"]
    assert restored.excluded_ids == ["p3"]
    assert restored.draws[0].round_pass_ids == {1: ["p2", "p1"], 2: ["p2"]}
    assert restored.draws[0].winners == ["p2"]


def test_load_snapshot_missing_file_returns_none(tmp_path):
    store = SessionStore(snapshot_path=tmp_path / "does_not_exist.json")
    assert store.load_snapshot() is None
    assert store.get_session() is None


def test_clear_removes_snapshot_file(tmp_path):
    snapshot_path = tmp_path / "session_snapshot.json"
    store = SessionStore(snapshot_path=snapshot_path)
    store.set_session(_make_session())
    assert snapshot_path.exists()

    store.clear()
    assert not snapshot_path.exists()
    assert store.get_session() is None
