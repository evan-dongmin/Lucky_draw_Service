from app.mc import SITUATION_TAGS, STATIC_TEMPLATES, MCAgent, _validate_llm_templates, render_line


def test_all_situation_tags_have_static_fallback():
    for tag in SITUATION_TAGS:
        assert STATIC_TEMPLATES.get(tag), f"{tag}에 정적 폴백 멘트가 없습니다"


def test_render_line_substitutes_known_placeholder():
    result = render_line("{team}이(가) 선두입니다", team="개발팀")
    assert result == "개발팀이(가) 선두입니다"


def test_render_line_does_not_crash_on_missing_placeholder():
    result = render_line("{team}과 {unknown_key} 상황", team="개발팀")
    assert "개발팀" in result
    assert "{unknown_key}" in result  # 크래시 대신 원문 유지


def test_validate_llm_templates_rejects_disallowed_placeholders():
    lines = [
        "{team}이(가) 선두입니다",  # 허용됨
        "{employee_name}님 축하합니다",  # 실명 유출 위험 -- 거부되어야 함
        "완성된 문장, 플레이스홀더 없음",  # 허용됨
    ]
    valid = _validate_llm_templates(lines)
    assert "{team}이(가) 선두입니다" in valid
    assert "완성된 문장, 플레이스홀더 없음" in valid
    assert not any("employee_name" in line for line in valid)


def test_mc_agent_without_api_key_uses_static_pool(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.XAI_API_KEY", "")
    monkeypatch.setattr("app.config.GEMINI_API_KEY", "")
    agent = MCAgent(cache_path=tmp_path / "mc_cache.json")
    assert agent.has_llm is False

    agent.pregenerate()  # API 키 없음 -- 조용히 스킵되어야 함(예외 없음)

    for tag in SITUATION_TAGS:
        line = agent.pick_line(tag, team="개발팀", participant_count=250, department_count=6)
        assert line, f"{tag}에 대해 빈 멘트가 반환됨"


def test_mc_agent_pregenerate_falls_back_on_llm_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.XAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr("app.config.GEMINI_API_KEY", "")
    agent = MCAgent(cache_path=tmp_path / "mc_cache.json")
    assert agent.has_llm is True

    def _boom(self, tag, count):
        raise RuntimeError("네트워크 실패 시뮬레이션")

    monkeypatch.setattr(MCAgent, "_call_llm_for_tag", _boom)
    agent.pregenerate()  # 예외가 전파되면 안 됨

    for tag in SITUATION_TAGS:
        line = agent.pick_line(tag)
        assert line  # 정적 폴백으로 계속 동작해야 함


def test_mc_agent_pregenerate_uses_llm_lines_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.XAI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr("app.config.GEMINI_API_KEY", "")
    agent = MCAgent(cache_path=tmp_path / "mc_cache.json")

    def _fake_call(self, tag, count):
        return [f"LLM생성-{tag}-1", f"LLM생성-{tag}-2"]

    monkeypatch.setattr(MCAgent, "_call_llm_for_tag", _fake_call)
    agent.pregenerate(tags=["opening"])

    line = agent.pick_line("opening")
    assert line.startswith("LLM생성-opening")
    assert (tmp_path / "mc_cache.json").exists()


def test_mc_agent_falls_back_to_gemini_when_grok_fails(tmp_path, monkeypatch):
    """Grok 호출이 실패(한도 초과 등)하면 Gemini로 자동 대체되어야 한다."""
    monkeypatch.setattr("app.config.XAI_API_KEY", "fake-grok-key")
    monkeypatch.setattr("app.config.GEMINI_API_KEY", "fake-gemini-key")
    agent = MCAgent(cache_path=tmp_path / "mc_cache.json")

    def _grok_boom(self, tag, count):
        raise RuntimeError("Grok 한도 초과 시뮬레이션")

    def _fake_gemini(self, tag, count):
        return [f"Gemini생성-{tag}-1"]

    monkeypatch.setattr(MCAgent, "_call_grok", _grok_boom)
    monkeypatch.setattr(MCAgent, "_call_gemini", _fake_gemini)

    lines = agent._call_llm_for_tag("opening", 1)
    assert lines == ["Gemini생성-opening-1"]


def test_mc_agent_uses_gemini_directly_when_only_gemini_key_set(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.XAI_API_KEY", "")
    monkeypatch.setattr("app.config.GEMINI_API_KEY", "fake-gemini-key")
    agent = MCAgent(cache_path=tmp_path / "mc_cache.json")
    assert agent.has_llm is True

    def _fake_gemini(self, tag, count):
        return [f"Gemini생성-{tag}-1"]

    monkeypatch.setattr(MCAgent, "_call_gemini", _fake_gemini)
    lines = agent._call_llm_for_tag("opening", 1)
    assert lines == ["Gemini생성-opening-1"]


def test_mc_agent_pick_line_rotates_without_immediate_repeat():
    agent = MCAgent(cache_path=None)
    tag = "opening"
    seen = [agent.pick_line(tag) for _ in range(len(STATIC_TEMPLATES[tag]))]
    assert len(set(seen)) == len(STATIC_TEMPLATES[tag])


def test_mc_agent_load_cache_from_disk(tmp_path):
    cache_path = tmp_path / "mc_cache.json"
    cache_path.write_text('{"opening": ["캐시된 멘트"]}', encoding="utf-8")
    agent = MCAgent(cache_path=cache_path)
    agent.load_cache()
    assert agent.pick_line("opening") == "캐시된 멘트"
