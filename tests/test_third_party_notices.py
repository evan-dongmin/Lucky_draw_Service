"""제출 요건("사용한 오픈소스 목록 및 라이선스 정리")을 지키는 문서가
실제 의존성과 어긋나지 않는지 검사한다.

의존성을 올릴 때 문서를 같이 안 고치는 것은 아주 흔한 실수이고, 사람
눈으로는 절대 안 잡힌다(실제로 pydantic 2.12.4 -> 2.12.5 상향이 문서에
반영돼 있지 않은 것을 이 테스트를 넣으며 발견했다).
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
REQUIREMENTS = ROOT / "requirements.txt"


def _pinned_requirements() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        # uvicorn[standard] -> uvicorn, qrcode[pil] -> qrcode
        pins[name.split("[")[0].strip()] = version.strip()
    return pins


def _documented_versions() -> dict[str, str]:
    """문서 표의 (패키지, 버전). 문서는 requirements와 같은 표기
    (`uvicorn[standard]`)를 쓰므로 여기서도 extras를 떼고 이름만 남긴다."""
    text = NOTICES.read_text(encoding="utf-8")
    documented: dict[str, str] = {}
    for row in re.finditer(r"^\|\s*([\w.\[\]-]+)\s*\|\s*([\w.+-]+)\s*\|", text, re.M):
        name = row.group(1).split("[")[0].strip()
        documented[name] = row.group(2).strip()
    return documented


def test_every_pinned_dependency_is_documented():
    pins = _pinned_requirements()
    documented = _documented_versions()
    missing = sorted(set(pins) - set(documented))
    assert not missing, f"THIRD_PARTY_NOTICES.md에 빠진 패키지: {missing}"


def test_documented_versions_match_requirements():
    pins = _pinned_requirements()
    documented = _documented_versions()
    mismatched = {
        name: (documented[name], version)
        for name, version in pins.items()
        if name in documented and documented[name] != version
    }
    assert not mismatched, f"문서와 실제 버전이 다릅니다 (문서, 실제): {mismatched}"
