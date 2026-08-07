"""캐릭터/카트 선택: 참가자가 직접 고르는 정체성 + 예측 점수 특성.

처음에는 부서(팀) 단위로 "특수능력"을 자동 배정했지만, 참가자가 자기
카트를 직접 고르고 싶어 해 개인 단위 선택으로 확장했다.

**레이스 결과에는 어떤 영향도 주지 않는다** -- 최종 순위·통과 여부는
여전히 fairness.py가 커밋된 시드만으로 계산한다(race.py의 카트별 페이스
지수, static/stage.js의 팀 특수능력과 동일한 원칙). 능력은 사람이 고르는
값이라 순위에 영향을 주는 순간 "능력 잘 고른 사람이 유리한 추첨"이 되어
공정성이 깨진다(시드에서 파생되는 장애물과는 성격이 다르다).

대신 **예측 게임의 점수 규칙을 살짝 비트는 특성**을 준다(작업계획서 §12-3).
"골라도 아무 차이가 없다"는 사용자 피드백에 대한 답이면서, 추첨 공정성은
그대로 지키는 절충점이다. 예측 리더보드가 실제 당첨자를 정하는 구조라
점수에 영향을 주면 선택이 실제로 유의미해진다.

**배수 폭은 의도적으로 좁게(주력 배수 ±30% 이내) 잡았다** -- 그 이상이면
"무엇을 맞혔는가"보다 "무엇을 골랐는가"가 중요해져 예측 게임이 아니게 된다.
2배가 붙은 두 특성(shield/stardust)은 참여 보상·통과 보상처럼 **원래 금액이
작은 항목**에만 적용돼 절대 점수 영향이 크지 않다.

static/stage.js의 ABILITY_ROSTER와 id·label·emoji가 정확히 같아야 한다
(참가자가 고르지 않았을 때 쓰는 부서 기반 폴백과 동일한 시각 언어를
공유하기 위함). 하나를 바꾸면 반드시 다른 쪽도 함께 바꿀 것.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AbilityEffect:
    """예측 채점에 적용되는 배수 묶음(app/predictions.py: score_round).

    모든 필드는 "곱하는 값"이고 기본값 1.0 = 효과 없음이라, 새 능력을
    추가할 때 관련 없는 항목은 그냥 비워두면 된다.
    """

    # 이 라운드들의 예측 점수에 predict_multiplier를 곱한다
    predict_rounds: tuple[int, ...] = ()
    predict_multiplier: float = 1.0
    top_hit_multiplier: float = 1.0  # 1위를 정확히 맞혔을 때
    runner_up_multiplier: float = 1.0  # 2~3위를 맞혔을 때
    floor_multiplier: float = 1.0  # 순위표 밖(참여 보상)일 때
    minority_bonus_add: float = 0.0  # 소수파 보너스 배수에 가산
    team_multiplier: float = 1.0  # 부서 순위 보상
    finish_multiplier: float = 1.0  # 결승선 통과 보상


# id -> (표시용 설명, 실제 효과). 설명 문구는 모바일 카트 선택 화면에
# 그대로 노출된다 -- 효과를 모르면 "선택"이 아니라 그냥 아바타 고르기다.
ABILITY_EFFECTS: dict[str, AbilityEffect] = {
    "nitro": AbilityEffect(predict_rounds=(1,), predict_multiplier=1.25),
    "shield": AbilityEffect(floor_multiplier=2.0),
    "spark": AbilityEffect(top_hit_multiplier=1.15),
    "draft": AbilityEffect(runner_up_multiplier=1.30),
    "lucky": AbilityEffect(minority_bonus_add=0.3),
    "wave": AbilityEffect(team_multiplier=1.5),
    "stardust": AbilityEffect(finish_multiplier=2.0),
    "rocket": AbilityEffect(predict_rounds=(3,), predict_multiplier=1.20),
}

NEUTRAL_ABILITY = AbilityEffect()

CHARACTER_ROSTER: list[dict[str, str]] = [
    {
        "id": "nitro",
        "label": "니트로 부스트",
        "emoji": "🔥",
        "effect": "1라운드 예측 점수 +25%",
        "style": "초반 집중형",
    },
    {
        "id": "shield",
        "label": "배리어 실드",
        "emoji": "🛡️",
        "effect": "예측이 순위 밖일 때 받는 참여 점수 2배",
        "style": "안정 지향",
    },
    {
        "id": "spark",
        "label": "스파크 러시",
        "emoji": "⚡",
        "effect": "1위를 정확히 맞히면 +15%",
        "style": "하이리스크",
    },
    {
        "id": "draft",
        "label": "슬립스트림",
        "emoji": "🌪️",
        "effect": "고른 대상이 2~3위일 때 +30%",
        "style": "차점 노림",
    },
    {
        "id": "lucky",
        "label": "럭키 드래프트",
        "emoji": "💎",
        "effect": "소수파 보너스 배수 +0.3",
        "style": "역배 전문",
    },
    {
        "id": "wave",
        "label": "타이달 웨이브",
        "emoji": "🌊",
        "effect": "우리 팀 순위 보상 +50%",
        "style": "팀플레이형",
    },
    {
        "id": "stardust",
        "label": "스타더스트",
        "emoji": "⭐",
        "effect": "결승선 통과 보상 2배",
        "style": "생존 보상형",
    },
    {
        "id": "rocket",
        "label": "로켓 대시",
        "emoji": "🚀",
        "effect": "결선(3라운드) 예측 점수 +20%",
        "style": "막판 역전형",
    },
]

CHARACTER_IDS = {c["id"] for c in CHARACTER_ROSTER}


def effect_for(character_id: str | None) -> AbilityEffect:
    """카트 id -> 효과. 미선택(None)이나 모르는 id는 **중립 1.0배**다.

    카트를 안 고른 사람에게는 부서 기반 폴백 아이콘이 화면에 붙지만,
    그건 표시용일 뿐 점수 배수는 붙지 않는다 -- 안 고른 사람이 우연히
    유리해지면 "고르는 재미"를 주려던 취지가 뒤집힌다.
    """
    if character_id is None:
        return NEUTRAL_ABILITY
    return ABILITY_EFFECTS.get(character_id, NEUTRAL_ABILITY)
