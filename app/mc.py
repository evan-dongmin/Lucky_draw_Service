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
    "countdown",
    "race_progress",
    "close_call",
    "final_lap",
    "photo_finish",
    "department_rank_shift",
    "round_pass_announce",
    "elimination",
    "prediction_open",
    "ability_trigger",
    "prediction_result",
    "prediction_champion",
    "final_announce",
    "podium",
    "verification",
)

# 정적 폴백 풀 -- API 키가 없거나 LLM 호출이 실패해도 항상 이 풀로 동작한다.
# race_progress/department_rank_shift는 레이스 도중 실시간 이벤트(추월·선두
# 교체)에 반응해 자주 호출되므로, 다른 태그보다 폭을 넓게 잡았다.
STATIC_TEMPLATES: dict[str, list[str]] = {
    "opening": [
        "안녕하세요! 오늘의 추첨, 지금 시작합니다.",
        "긴장되시나요? 오늘의 결과는 이미 무작위 시드로 정해져 있습니다.",
        "총 {participant_count}명, {department_count}개 부서가 오늘의 주인공입니다!",
        "룰이 정해졌고, 이제 결과만 남았습니다. 시작하겠습니다!",
        "장애물 하나까지도 이미 정해져 있는 완전히 결정론적인 레이스입니다.",
        "자, 카트들이 출발선에 섰습니다. 준비되셨나요?",
    ],
    "countdown": [
        "출발 신호등에 불이 들어옵니다. 모두 숨죽여 주세요!",
        "{round}라운드, 카트들이 출발선에 정렬했습니다!",
        "다섯 개의 등이 하나씩 켜집니다... 곧 출발합니다!",
        "엔진 소리가 올라갑니다. 준비하세요!",
        "자, 이번 라운드입니다. 눈 깜빡이지 마세요!",
        "긴장의 순간입니다. 라이트 아웃까지 몇 초 남지 않았습니다!",
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
    "close_call": [
        "통과선 바로 앞! 저 카트, 한 뼘이 모자랍니다!",
        "아슬아슬합니다. 지금 밀리면 여기서 끝입니다!",
        "{team} 카트가 컷라인에 걸쳐 있습니다. 조금만 더!",
        "숨 막히는 순간입니다. 몇 센티 차이로 운명이 갈립니다.",
        "붉은 구역에 갇힌 카트들, 마지막 힘을 짜냅니다!",
        "여기서 한 대만 더 제치면 살아남습니다!",
    ],
    "final_lap": [
        "마지막 구간입니다! 전력 질주 들어갑니다!",
        "결승선이 눈앞입니다. 지금부터가 진짜입니다!",
        "파이널 랩! 여기서 순위가 결정됩니다!",
        "모두 마지막 힘을 쏟아붓고 있습니다!",
        "이제 되돌릴 수 없습니다. 끝까지 밀어붙입니다!",
        "{team}, 지금이 마지막 기회입니다!",
    ],
    "photo_finish": [
        "포토 피니시! 눈으로는 판별이 안 됩니다!",
        "거의 동시에 들어왔습니다! 확인해보겠습니다!",
        "이런 접전은 처음입니다. 결승선 사진을 봐야겠습니다!",
        "찰나의 차이였습니다. 관중석이 얼어붙었습니다!",
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
    "ability_trigger": [
        "{team}의 {ability}가(이) 발동합니다!",
        "{team} 카트에서 {ability} 이펙트가 터져 나옵니다!",
        "지금 {team}이(가) {ability}로 분위기를 바꿉니다!",
        "{ability}! {team}이(가) 순간적으로 치고 나갑니다!",
    ],
    "prediction_result": [
        "{round}라운드 예측 점수가 반영됐습니다! 폰에서 확인해보세요!",
        "1위 부서를 맞히신 분들, 축하합니다! 아깝게 빗나가신 분들도 점수는 들어갔습니다!",
        "이번 라운드는 아무도 0점이 아닙니다 -- 순위가 가까울수록 점수가 큽니다!",
        "리더보드가 방금 요동쳤습니다. 아직 끝나지 않았습니다!",
    ],
    "prediction_champion": [
        "오늘의 예측왕이 결정됐습니다!",
        "가장 많은 점수를 모은 참가자, 축하합니다!",
        "레이스를 가장 잘 읽어낸 분이 가려졌습니다!",
    ],
    "elimination": [
        "여기서 여정을 마치는 카트들이 있습니다. 큰 박수 부탁드립니다!",
        "아쉽게 이번 라운드에서 멈춰 섰습니다. 정말 잘 달렸습니다!",
        "탈락이 확정됐지만, 여기까지 온 것만으로도 대단합니다!",
        "다음 기회가 있습니다. 지금까지 정말 수고하셨습니다!",
    ],
    "prediction_open": [
        "예측 창이 열렸습니다! 지금 폰을 꺼내주세요!",
        "다음 라운드 예측 시간입니다. 누가 이길지 골라주세요!",
        "{round}라운드 예측을 받습니다. 남들과 다른 선택이 더 큰 점수입니다!",
        "표가 몰리는 쪽은 배점이 낮아집니다. 역배를 노려보시겠습니까?",
        "선택 창은 곧 닫힙니다. 서둘러 주세요!",
    ],
    "final_announce": [
        "드디어 최종 당첨자가 결정되었습니다!",
        "오늘의 주인공 {winner_count}명을 발표합니다!",
        "축하합니다! 결승선을 통과한 여러분이 오늘의 당첨자입니다.",
        "긴 여정이었습니다. {winner_count}명의 당첨자, 진심으로 축하드립니다!",
    ],
    "podium": [
        "시상대 위 주인공들입니다. 큰 박수 부탁드립니다!",
        "오늘의 챔피언, 정말 축하드립니다!",
        "{winner_count}명의 이름이 시상대에 올랐습니다!",
        "긴 레이스의 끝, 트로피는 이분들의 것입니다!",
    ],
    "verification": [
        "오늘의 결과는 참가자 명단과 무작위 시드만으로 계산된 것입니다.",
        "장애물에 부딪힌 것까지 포함해서, 모든 결과가 완전히 결정론적입니다.",
        "이 결과는 처음부터 정해져 있었고, 그 누구도 사후에 바꿀 수 없습니다.",
        "축하의 박수, 오늘의 주인공들에게 다시 한번 보내주세요!",
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
    "round",
    "count",
    "ability",
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_line(template: str, **params: Any) -> str:
    """플레이스홀더 치환. 누락된 키가 있어도 크래시 없이 그대로 남긴다."""
    return template.format_map(_SafeDict(**params))


def _required_fields(template: str) -> set[str] | None:
    """줄에 들어 있는 `{placeholder}` 이름 집합. 중괄호가 깨져 있으면(예:
    짝이 안 맞는 `{`) None을 돌려준다 -- 렌더링 시점에 크래시 나기 전에
    호출부가 걸러낼 수 있도록."""
    try:
        return {name for _, name, _, _ in Formatter().parse(template) if name}
    except ValueError:
        return None


def _validate_llm_templates(lines: list[str]) -> list[str]:
    """LLM 응답에서 허용되지 않은 플레이스홀더가 포함된 줄은 버린다
    (실명/사번 유출 방지의 마지막 방어선)."""
    valid: list[str] = []
    for line in lines:
        fields = _required_fields(line)
        if fields is None:
            logger.warning("중괄호가 깨진 LLM 멘트 폐기: %s", line)
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
        return bool(config.XAI_API_KEY or config.GEMINI_API_KEY)

    @staticmethod
    def _build_prompt(tag: str, count: int) -> str:
        allowed = ", ".join(f"{{{p}}}" for p in ALLOWED_PLACEHOLDERS)
        return (
            "사내 타운홀 경품 추첨 행사의 AI 진행자 멘트를 만들어줘.\n"
            f"상황: {tag}\n"
            f"서로 다른 멘트 {count}개를 한 줄씩 만들어줘. 각 줄은 완성된 한국어 문장이어야 해.\n"
            f"실제 이름이나 숫자는 절대 쓰지 말고, 필요하면 다음 플레이스홀더만 써: {allowed}\n"
            "그 외 다른 중괄호 표현은 쓰지 마. 설명 없이 멘트만 한 줄씩 출력해."
        )

    @staticmethod
    def _split_lines(text: str) -> list[str]:
        return [ln.strip("- ").strip() for ln in text.splitlines() if ln.strip()]

    def _call_grok(self, tag: str, count: int) -> list[str]:
        """xAI Grok (OpenAI 호환 API) 호출."""
        from openai import OpenAI

        client = OpenAI(api_key=config.XAI_API_KEY, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model=config.XAI_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": self._build_prompt(tag, count)}],
        )
        text = response.choices[0].message.content or ""
        return _validate_llm_templates(self._split_lines(text))

    def _call_gemini(self, tag: str, count: int) -> list[str]:
        """Google Gemini 호출."""
        from google import genai

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=self._build_prompt(tag, count),
        )
        text = response.text or ""
        return _validate_llm_templates(self._split_lines(text))

    def _call_llm_for_tag(self, tag: str, count: int) -> list[str]:
        """행사 전 사전 생성 전용. 반드시 익명 슬롯만 사용하도록 프롬프트로 강제한다.

        xAI Grok을 우선 사용한다(무료 크레딧 소진/한도 초과 등으로 실패하면
        Gemini로 자동 대체). 두 키 모두 없으면 pregenerate()의 has_llm 체크에서
        이미 걸러지므로 여기까지 오지 않는다."""
        if config.XAI_API_KEY:
            try:
                return self._call_grok(tag, count)
            except Exception:  # noqa: BLE001 - Grok 한도 초과/오류 시 Gemini로 대체
                logger.warning("Grok 호출 실패(%s) -- Gemini로 대체 시도", tag, exc_info=True)
        return self._call_gemini(tag, count)

    def pregenerate(self, tags: list[str] | None = None, lines_per_tag: int = 12) -> None:
        """행사 시작 전 1회 호출. 실패해도 예외를 전파하지 않고 정적 폴백을 유지한다.

        race_progress/department_rank_shift는 레이스 도중 실시간 이벤트(추월·
        선두 교체)마다 호출되어 소모가 빠르므로 기본 요청 수를 12줄로 늘렸다
        (기존 6줄 -- 10분 데모에서도 금방 티가 나던 반복을 줄이기 위함)."""
        tags = tags or list(SITUATION_TAGS)
        self._bags.clear()  # 새로 채워질 풀 기준으로 셔플백을 다시 만든다
        if not self.has_llm:
            logger.info("XAI_API_KEY/GEMINI_API_KEY 없음 -- 정적 폴백 멘트만 사용")
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
        같은 줄이 한 사이클 안에서 두 번 나오지는 않는다.

        **호출부가 이번에 실제로 값을 준 플레이스홀더만 요구하는 줄로
        후보를 미리 거른다** -- 그래야 "{team}"처럼 값을 안 준 자리가
        화면에 그대로 남는 사고가 안 난다(실제로 close_call/race_progress/
        final_announce 등이 team·winner_count 없이 호출되는 경로가 있었다).
        같은 태그라도 호출마다 넘기는 파라미터 조합이 다를 수 있어, 셔플백은
        태그+파라미터 조합 단위로 따로 관리한다."""
        pool = self.pool_for(tag)
        if not pool:
            return ""
        available = {k for k, v in params.items() if v is not None}
        compatible = [line for line in pool if (_required_fields(line) or set()) <= available]
        if not compatible:
            # 이 조합으로 채울 수 있는 줄이 하나도 없다 -- 그래도 뭔가는
            # 보여줘야 하므로 폴 전체로 물러난다(빈 자리는 render_line이
            # 안전하게 그대로 남긴다).
            compatible = pool
        bag_key = f"{tag}:{','.join(sorted(available))}"
        bag = self._bags.get(bag_key)
        if not bag:
            bag = deque(compatible)
            random.shuffle(bag)
            self._bags[bag_key] = bag
        line = bag.popleft()
        return render_line(line, **params)
