"""MC Agent: 해설 멘트 생성.

원칙(기획안 §5):
- 실명·사번·부서명은 외부 LLM API로 절대 전송하지 않는다. LLM에는 익명 슬롯
  템플릿("{team}의 {count}번째 카트가...") 생성만 맡기고, 실제 값 치환은
  전부 로컬에서 수행한다.
- LLM 호출은 행사 "전" 사전 배치 생성으로만 한다. 레이스 도중 실시간 API
  호출은 하지 않는다 (지연·실패가 라이브 진행을 막지 않도록).
- API 키가 없거나 호출이 실패해도 전 상황 태그에 대해 정적 폴백이 항상
  존재해 서비스가 완전히 동작해야 한다.
"""

from __future__ import annotations

import json
import logging
import random
from collections import deque
from pathlib import Path
from string import Formatter
from typing import Any

from app import config

logger = logging.getLogger("lucky_draw.mc")

SITUATION_TAGS = (
    "opening",
    "race_progress",
    "department_rank_shift",
    "round_pass_announce",
    "final_announce",
    "verification",
)

# 정적 폴백 풀 -- API 키가 없거나 LLM 호출이 실패해도 항상 이 풀로 동작한다.
# race_progress/department_rank_shift는 레이스 도중 실시간 이벤트(추월·선두
# 교체)에 반응해 자주 호출되므로, 다른 태그보다 폭을 넓게 잡았다.
STATIC_TEMPLATES: dict[str, list[str]] = {
    "opening": [
        "안녕하세요! 오늘의 추첨, 지금 시작합니다.",
        "긴장되시나요? 이 봉인된 커밋 해시가 오늘 추첨의 증거입니다.",
        "총 {participant_count}명, {department_count}개 부서가 오늘의 주인공입니다!",
        "룰이 정해졌고, 이제 결과만 남았습니다. 시작하겠습니다!",
        "화면에 뜬 해시값, 잘 기억해두세요. 나중에 직접 검증하실 수 있습니다.",
        "자, 카트들이 출발선에 섰습니다. 준비되셨나요?",
    ],
    "race_progress": [
        "지금 트랙 위 경쟁이 치열합니다!",
        "{team} 소속 카트들이 힘을 내고 있습니다.",
        "순위가 계속 바뀌고 있어요, 끝까지 지켜봐 주세요!",
        "결승선이 가까워지고 있습니다!",
        "{team}이(가) 통과선 근처에서 안간힘을 쓰고 있습니다!",
        "이대로 갈까요, 아니면 막판 뒤집기가 나올까요?",
        "{team} 카트가 무서운 기세로 치고 올라옵니다!",
        "박빙입니다! 통과선 위아래로 순위가 뒤섞이고 있어요.",
    ],
    "department_rank_shift": [
        "{team}이(가) 선두로 올라섰습니다!",
        "부서 통과율 순위가 방금 뒤집혔습니다 -- {team}이 앞서갑니다!",
        "예상 밖의 전개! {team}이 저력을 보여주고 있습니다.",
        "{team}, 지금 이 순간 가장 뜨거운 부서입니다!",
        "선두가 바뀌었습니다! 지금은 {team}이 1위입니다.",
        "{team}이(가) 조용히 순위를 끌어올리고 있었네요!",
    ],
    "round_pass_announce": [
        "이번 라운드 통과자가 확정되었습니다!",
        "{pass_count}명이 다음 라운드로 진출합니다.",
        "치열했던 라운드가 끝났습니다. 통과선을 넘은 카트들, 축하합니다!",
        "여기까지 오신 {pass_count}분, 정말 대단합니다!",
    ],
    "final_announce": [
        "드디어 최종 당첨자가 결정되었습니다!",
        "오늘의 주인공 {winner_count}명을 발표합니다!",
        "축하합니다! 결승선을 통과한 여러분이 오늘의 당첨자입니다.",
        "긴 여정이었습니다. {winner_count}명의 당첨자, 진심으로 축하드립니다!",
    ],
    "verification": [
        "지금부터 시드를 공개하고, 커밋 해시와 일치하는지 함께 확인합니다.",
        "궁금하신 분은 QR로 접속해서 직접 검증해보실 수 있습니다.",
        "이 결과는 처음부터 정해져 있었고, 누구나 재계산으로 확인 가능합니다.",
        "조작은 불가능합니다 -- 여러분 모두가 지금 그 증거를 갖고 계십니다.",
    ],
}

# LLM에게 허용하는 치환 슬롯(익명 토큰만) -- 실명/사번은 여기 포함되지 않는다.
ALLOWED_PLACEHOLDERS = {
    "team",
    "participant_count",
    "department_count",
    "pass_count",
    "winner_count",
    "rank",
    "count",
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_line(template: str, **params: Any) -> str:
    """플레이스홀더 치환. 누락된 키가 있어도 크래시 없이 그대로 남긴다."""
    return template.format_map(_SafeDict(**params))


def _validate_llm_templates(lines: list[str]) -> list[str]:
    """LLM 응답에서 허용되지 않은 플레이스홀더가 포함된 줄은 버린다
    (실명/사번 유출 방지의 마지막 방어선)."""
    valid: list[str] = []
    formatter = Formatter()
    for line in lines:
        try:
            fields = {name for _, name, _, _ in formatter.parse(line) if name}
        except ValueError:
            continue
        if fields - ALLOWED_PLACEHOLDERS:
            logger.warning("허용되지 않은 플레이스홀더로 LLM 멘트 폐기: %s", line)
            continue
        valid.append(line)
    return valid


class MCAgent:
    def __init__(self, cache_path: Path | None = None) -> None:
        self.cache_path = cache_path or (config.DATA_DIR / "mc_cache.json")
        self._cache: dict[str, list[str]] = {}
        self._bags: dict[str, deque[str]] = {}

    @property
    def has_llm(self) -> bool:
        return bool(config.ANTHROPIC_API_KEY)

    def _call_llm_for_tag(self, tag: str, count: int) -> list[str]:
        """행사 전 사전 생성 전용. 반드시 익명 슬롯만 사용하도록 프롬프트로 강제한다."""
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        allowed = ", ".join(f"{{{p}}}" for p in ALLOWED_PLACEHOLDERS)
        prompt = (
            "사내 타운홀 경품 추첨 행사의 AI 진행자 멘트를 만들어줘.\n"
            f"상황: {tag}\n"
            f"서로 다른 멘트 {count}개를 한 줄씩 만들어줘. 각 줄은 완성된 한국어 문장이어야 해.\n"
            f"실제 이름이나 숫자는 절대 쓰지 말고, 필요하면 다음 플레이스홀더만 써: {allowed}\n"
            "그 외 다른 중괄호 표현은 쓰지 마. 설명 없이 멘트만 한 줄씩 출력해."
        )
        response = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        lines = [ln.strip("- ").strip() for ln in text.splitlines() if ln.strip()]
        return _validate_llm_templates(lines)

    def pregenerate(self, tags: list[str] | None = None, lines_per_tag: int = 12) -> None:
        """행사 시작 전 1회 호출. 실패해도 예외를 전파하지 않고 정적 폴백을 유지한다.

        race_progress/department_rank_shift는 레이스 도중 실시간 이벤트(추월·
        선두 교체)마다 호출되어 소모가 빠르므로 기본 요청 수를 12줄로 늘렸다
        (기존 6줄 -- 5분 데모에서도 금방 티가 나던 반복을 줄이기 위함)."""
        tags = tags or list(SITUATION_TAGS)
        self._bags.clear()  # 새로 채워질 풀 기준으로 셔플백을 다시 만든다
        if not self.has_llm:
            logger.info("ANTHROPIC_API_KEY 없음 -- 정적 폴백 멘트만 사용")
            return
        for tag in tags:
            try:
                lines = self._call_llm_for_tag(tag, lines_per_tag)
                if lines:
                    self._cache[tag] = lines
                    logger.info("MC 멘트 사전생성 완료: %s (%d줄)", tag, len(lines))
            except Exception:  # noqa: BLE001 - 라이브 진행을 막지 않도록 폭넓게 방어
                logger.exception("MC 멘트 사전생성 실패(%s) -- 정적 폴백으로 대체", tag)
        self._save_cache()

    def _save_cache(self) -> None:
        if not self._cache:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_cache(self) -> None:
        if self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._bags.clear()

    @property
    def cached_tags(self) -> list[str]:
        return list(self._cache.keys())

    def pool_for(self, tag: str) -> list[str]:
        return self._cache.get(tag) or STATIC_TEMPLATES.get(tag, [])

    def pick_line(self, tag: str, **params: Any) -> str:
        """셔플백 방식: 폴 전체를 무작위 순서로 한 번씩 다 쓴 뒤에만 다시
        섞는다. 라운드로빈보다 반복 시 티가 덜 나면서도(매번 다른 순서),
        같은 줄이 한 사이클 안에서 두 번 나오지는 않는다."""
        pool = self.pool_for(tag)
        if not pool:
            return ""
        bag = self._bags.get(tag)
        if not bag:
            bag = deque(pool)
            random.shuffle(bag)
            self._bags[tag] = bag
        line = bag.popleft()
        return render_line(line, **params)
