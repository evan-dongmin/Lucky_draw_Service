from __future__ import annotations

import csv
import io
import random

from app.models import Participant

_HEADER_ALIASES = {
    "id": {"id", "사번", "번호", "employee_id", "no"},
    "name": {"name", "이름", "성명"},
    "team": {"team", "팀", "부서", "소속"},
}

_DECODE_CANDIDATES = ("utf-8-sig", "utf-8", "cp949")


class RosterParseError(ValueError):
    def __init__(self, message: str, duplicate_ids: list[str] | None = None) -> None:
        super().__init__(message)
        self.duplicate_ids = duplicate_ids or []


def decode_roster_bytes(data: bytes) -> str:
    """CSV/xlsx 업로드 바이트를 UTF-8 또는 CP949(엑셀 기본 저장 인코딩)로 디코딩."""
    last_error: UnicodeDecodeError | None = None
    for encoding in _DECODE_CANDIDATES:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RosterParseError(f"지원하지 않는 인코딩입니다: {last_error}")


def _detect_delimiter(sample: str) -> str:
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    if "\t" in first_line:
        return "\t"
    return ","


def _match_header(fields: list[str]) -> dict[str, int] | None:
    normalized = [f.strip().lower() for f in fields]
    column_index: dict[str, int] = {}
    for key, aliases in _HEADER_ALIASES.items():
        for i, field_name in enumerate(normalized):
            if field_name in aliases:
                column_index[key] = i
                break
    if "id" in column_index and "name" in column_index:
        return column_index
    return None


def parse_roster_text(text: str) -> list[Participant]:
    """CSV 텍스트 또는 스프레드시트에서 붙여넣은 탭 구분 텍스트를 파싱한다.

    헤더 행(선택)을 인식하며, 없으면 열 순서를 id,name,team으로 가정한다.
    공백만 있는 줄은 건너뛰고, 중복 id는 RosterParseError로 보고한다.
    """
    text = text.lstrip("﻿")
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []

    column_index = _match_header(rows[0])
    start = 0
    if column_index is not None:
        start = 1
    else:
        column_index = {"id": 0, "name": 1, "team": 2}

    participants: list[Participant] = []
    seen_ids: dict[str, int] = {}
    duplicate_ids: list[str] = []

    for row in rows[start:]:
        if not any(cell.strip() for cell in row):
            continue

        def cell(key: str) -> str:
            idx = column_index.get(key)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        participant_id = cell("id")
        name = cell("name")
        team = cell("team")

        if not participant_id or not name:
            continue

        if participant_id in seen_ids:
            duplicate_ids.append(participant_id)
            continue
        seen_ids[participant_id] = 1
        participants.append(Participant(id=participant_id, name=name, team=team))

    if duplicate_ids:
        raise RosterParseError(
            f"중복된 참가자 ID가 있습니다: {', '.join(sorted(set(duplicate_ids)))}",
            duplicate_ids=sorted(set(duplicate_ids)),
        )

    return participants


def validate_roster(participants: list[Participant]) -> list[str]:
    """명단 확정 전 경고를 반환한다 (차단하지 않음 -- 부서 미지정은
    departments.py가 '미지정' 그룹으로 흡수하지만, 행사 전에 고쳐두는 편이 낫다)."""
    warnings: list[str] = []
    if not participants:
        return ["참가자가 없습니다"]

    blank_team_ids = [p.id for p in participants if not p.team.strip()]
    if blank_team_ids:
        shown = ", ".join(blank_team_ids[:10])
        more = " 등" if len(blank_team_ids) > 10 else ""
        warnings.append(f"부서가 비어 있는 참가자 {len(blank_team_ids)}명: {shown}{more}")

    return warnings


def parse_roster_bytes(data: bytes) -> list[Participant]:
    return parse_roster_text(decode_roster_bytes(data))


def parse_roster_xlsx(data: bytes) -> list[Participant]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = workbook.active
    rows: list[list[str]] = []
    for row in sheet.iter_rows(values_only=True):
        rows.append(["" if cell is None else str(cell) for cell in row])

    lines = []
    for row in rows:
        lines.append(",".join(_csv_escape(cell) for cell in row))
    return parse_roster_text("\n".join(lines))


def _csv_escape(value: str) -> str:
    if "," in value or '"' in value:
        return '"' + value.replace('"', '""') + '"'
    return value


_SAMPLE_SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
_SAMPLE_GIVEN = ["민준", "서연", "지호", "지우", "예은", "도윤", "하은", "시우", "채원", "은우"]
_SAMPLE_TEAMS = ["개발팀", "기획팀", "디자인팀", "영업팀", "인사팀", "재무팀", "마케팅팀", "고객지원팀"]


def generate_sample_participants(count: int = 250, seed: int | None = 42) -> list[Participant]:
    """데모/테스트용 가상 참가자 목록을 생성한다."""
    rng = random.Random(seed)
    participants: list[Participant] = []
    for i in range(1, count + 1):
        name = rng.choice(_SAMPLE_SURNAMES) + rng.choice(_SAMPLE_GIVEN)
        team = rng.choice(_SAMPLE_TEAMS)
        participants.append(Participant(id=f"P{i:04d}", name=name, team=team))
    return participants
