import pytest

from app.roster import (
    RosterParseError,
    decode_roster_bytes,
    generate_sample_participants,
    parse_roster_bytes,
    parse_roster_text,
)


def test_parse_with_korean_header():
    text = "사번,이름,팀\nP001,김철수,개발팀\nP002,이영희,기획팀\n"
    participants = parse_roster_text(text)
    assert [p.id for p in participants] == ["P001", "P002"]
    assert participants[0].name == "김철수"
    assert participants[0].team == "개발팀"


def test_parse_without_header_assumes_id_name_team_order():
    text = "P001,김철수,개발팀\nP002,이영희,기획팀\n"
    participants = parse_roster_text(text)
    assert len(participants) == 2
    assert participants[1].id == "P002"


def test_parse_skips_blank_lines_and_trims_whitespace():
    text = "id,name,team\n P001 , 김철수 , 개발팀 \n\n\nP002,이영희,기획팀\n   \n"
    participants = parse_roster_text(text)
    assert len(participants) == 2
    assert participants[0].id == "P001"
    assert participants[0].name == "김철수"
    assert participants[0].team == "개발팀"


def test_parse_tab_separated_paste():
    text = "id\tname\tteam\nP001\t김철수\t개발팀\nP002\t이영희\t기획팀\n"
    participants = parse_roster_text(text)
    assert len(participants) == 2
    assert participants[0].team == "개발팀"


def test_duplicate_ids_raise_error():
    text = "id,name,team\nP001,김철수,개발팀\nP001,이영희,기획팀\n"
    with pytest.raises(RosterParseError) as exc_info:
        parse_roster_text(text)
    assert "P001" in exc_info.value.duplicate_ids


def test_rows_missing_id_or_name_are_skipped():
    text = "id,name,team\nP001,김철수,개발팀\n,이영희,기획팀\nP003,,디자인팀\n"
    participants = parse_roster_text(text)
    assert [p.id for p in participants] == ["P001"]


def test_decode_utf8_bytes():
    text = "id,name,team\nP001,김철수,개발팀\n"
    data = text.encode("utf-8")
    assert decode_roster_bytes(data) == text


def test_decode_utf8_sig_bom_bytes():
    text = "id,name,team\nP001,김철수,개발팀\n"
    data = text.encode("utf-8-sig")
    participants = parse_roster_bytes(data)
    assert participants[0].id == "P001"


def test_decode_cp949_bytes():
    text = "id,name,team\nP001,김철수,개발팀\n"
    data = text.encode("cp949")
    decoded = decode_roster_bytes(data)
    assert decoded == text
    participants = parse_roster_bytes(data)
    assert participants[0].name == "김철수"


def test_generate_sample_participants_deterministic_and_unique():
    batch_a = generate_sample_participants(250, seed=42)
    batch_b = generate_sample_participants(250, seed=42)
    assert len(batch_a) == 250
    ids = [p.id for p in batch_a]
    assert len(set(ids)) == 250
    assert [p.id for p in batch_a] == [p.id for p in batch_b]
    assert [p.name for p in batch_a] == [p.name for p in batch_b]


def test_generate_sample_participants_roundtrips_through_parser():
    participants = generate_sample_participants(250, seed=1)
    csv_text = "id,name,team\n" + "\n".join(
        f"{p.id},{p.name},{p.team}" for p in participants
    )
    parsed = parse_roster_text(csv_text)
    assert len(parsed) == 250
    assert [p.id for p in parsed] == [p.id for p in participants]
