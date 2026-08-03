import pytest
from fastapi.testclient import TestClient

from app.main import app, store


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    """세션·예측 게임 스냅샷을 테스트마다 임시 파일로 격리해 테스트 간
    상태 오염과 실제 data/ 디렉터리 오염을 막는다."""
    from app import main as main_module

    original_session_path = store.snapshot_path
    original_prediction_path = main_module.prediction_snapshot_path
    original_gambling_path = main_module.gambling_snapshot_path
    store.snapshot_path = tmp_path / "session_snapshot.json"
    main_module.prediction_snapshot_path = tmp_path / "prediction_snapshot.json"
    main_module.gambling_snapshot_path = tmp_path / "gambling_snapshot.json"
    store.session = None
    main_module.prediction_engine.reset()
    main_module.gambling_engine.reset()
    main_module.predict_tokens.clear()
    yield
    store.session = None
    store.snapshot_path = original_session_path
    main_module.prediction_snapshot_path = original_prediction_path
    main_module.gambling_snapshot_path = original_gambling_path
    main_module.prediction_engine.reset()
    main_module.gambling_engine.reset()
    main_module.predict_tokens.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
