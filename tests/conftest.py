import pytest
from fastapi.testclient import TestClient

from app.main import app, store


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    """세션 스냅샷을 테스트마다 임시 파일로 격리해 테스트 간 상태 오염을 막는다."""
    original_path = store.snapshot_path
    store.snapshot_path = tmp_path / "session_snapshot.json"
    store.session = None
    yield
    store.session = None
    store.snapshot_path = original_path


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
