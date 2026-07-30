"""장애 리허설: 서버 재시작 시나리오에서 세션·예측 게임 상태가 이벤트
재생이 아니라 스냅샷 복원만으로 그대로 돌아오는지 검증한다."""

import json

from app import fairness
from app import main as main_module
from app.models import Session
from app.predictions import PredictionEngine
from app.roster import generate_sample_participants
from app.store import SessionStore


def test_session_snapshot_survives_process_restart(tmp_path):
    snapshot_path = tmp_path / "session_snapshot.json"
    participants = generate_sample_participants(50, seed=5)
    draw = fairness.compute_draw("restart-test", participants, draw_count=3, seed="restart-seed")
    session = Session(session_id="restart-test", participants=participants, draw_count=3)
    session.draws.append(draw)

    store_before_restart = SessionStore(snapshot_path=snapshot_path)
    store_before_restart.set_session(session)

    # "재시작" 흉내: 완전히 새로운 SessionStore 인스턴스(= 새 프로세스)로 동일 파일을 읽는다
    store_after_restart = SessionStore(snapshot_path=snapshot_path)
    restored = store_after_restart.load_snapshot()

    assert restored is not None
    assert restored.session_id == "restart-test"
    restored_draw = restored.draws[0]
    assert restored_draw.commit == draw.commit
    assert restored_draw.snapshot == draw.snapshot

    # 재계산으로도 동일 결과가 나와야 한다 (커밋-리빌 원칙: 이벤트 로그 재생이 아니라
    # seed+snapshot만으로 언제든 처음부터 다시 계산 가능해야 한다)
    fairness.reveal(restored_draw)
    assert fairness.verify_draw(restored_draw) is True


def test_prediction_snapshot_survives_process_restart():
    engine_before = PredictionEngine()
    engine_before.set_allocation("P1", {1: 20, 2: 30, 3: 50})
    engine_before.open_round(1, ["개발팀", "영업팀"])
    engine_before.set_target("P1", 1, "개발팀")
    engine_before.lock_round(1, seed="restart-pred-seed")
    engine_before.score_round(1, hit_set={"개발팀"})
    engine_before.open_round(2, ["개발팀", "영업팀"])

    payload = json.dumps(engine_before.to_dict(), ensure_ascii=False)

    # "재시작" 흉내: 완전히 새로운 PredictionEngine 인스턴스로 복원
    engine_after = PredictionEngine()
    engine_after.load_dict(json.loads(payload))

    assert engine_after.cards["P1"].score == engine_before.cards["P1"].score
    assert engine_after.cards["P1"].gain == engine_before.cards["P1"].gain
    assert engine_after.round_state == engine_before.round_state
    assert engine_after.round_candidates == engine_before.round_candidates


def test_main_module_save_and_load_prediction_snapshot_round_trip(tmp_path, monkeypatch):
    """main.py의 실제 저장/로드 함수(원자적 쓰기 포함)를 통한 왕복 검증."""
    monkeypatch.setattr(main_module, "prediction_snapshot_path", tmp_path / "prediction_snapshot.json")

    main_module.prediction_engine.reset()
    main_module.predict_tokens.clear()
    main_module.prediction_engine.set_allocation("P1", {1: 10, 2: 10, 3: 80})
    main_module.predict_tokens["tok-1"] = "P1"

    main_module.save_prediction_snapshot()

    # 메모리 상태를 지워서 "재시작 직후 빈 상태"를 흉내낸다
    main_module.prediction_engine.reset()
    main_module.predict_tokens.clear()
    assert "P1" not in main_module.prediction_engine.cards

    main_module.load_prediction_snapshot()

    assert main_module.prediction_engine.cards["P1"].alloc == {1: 10, 2: 10, 3: 80}
    assert main_module.predict_tokens["tok-1"] == "P1"

    # 정리
    main_module.prediction_engine.reset()
    main_module.predict_tokens.clear()
