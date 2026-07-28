from __future__ import annotations

import json
from pathlib import Path

from app.config import SNAPSHOT_PATH
from app.models import Session


class SessionStore:
    """단일 진행 세션의 인메모리 상태 + JSON 스냅샷 장애 복구."""

    def __init__(self, snapshot_path: Path = SNAPSHOT_PATH) -> None:
        self.snapshot_path = snapshot_path
        self.session: Session | None = None

    def set_session(self, session: Session) -> None:
        self.session = session
        self.save_snapshot()

    def get_session(self) -> Session | None:
        return self.session

    def clear(self) -> None:
        self.session = None
        if self.snapshot_path.exists():
            self.snapshot_path.unlink()

    def save_snapshot(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.session.to_dict() if self.session else None
        tmp_path = self.snapshot_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.snapshot_path)

    def load_snapshot(self) -> Session | None:
        if not self.snapshot_path.exists():
            self.session = None
            return None
        raw = self.snapshot_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if data is None:
            self.session = None
            return None
        self.session = Session.from_dict(data)
        return self.session
