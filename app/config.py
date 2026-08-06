import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_PATH = DATA_DIR / "session_snapshot.json"
PREDICTION_SNAPSHOT_PATH = DATA_DIR / "prediction_snapshot.json"
GAMBLING_SNAPSHOT_PATH = DATA_DIR / "gambling_snapshot.json"
CHARACTER_SNAPSHOT_PATH = DATA_DIR / "character_choices.json"

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# MC 멘트 사전생성용 LLM. xAI Grok을 우선 사용하고, 실패/한도 초과 시
# Gemini로 자동 대체한다(app/mc.py의 _call_llm_for_tag 참고). 둘 다 없어도
# 정적 폴백 멘트로 완전히 동작한다.
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-3-mini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
