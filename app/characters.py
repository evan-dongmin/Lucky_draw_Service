"""캐릭터/카트 선택: 참가자가 직접 고르는 순수 연출용 정체성.

처음에는 부서(팀) 단위로 "특수능력"을 자동 배정했지만, 참가자가 자기
카트를 직접 고르고 싶어 해 개인 단위 선택으로 확장했다. 팀 단위 배정과
마찬가지로 **결과에는 어떤 영향도 주지 않는다** -- 최종 순위·통과 여부는
여전히 fairness.py가 커밋된 시드만으로 계산한다(race.py의 카트별 페이스
지수, static/stage.js의 팀 특수능력과 동일한 원칙). 이 모듈은 "누가 어떤
캐릭터를 골랐는지"만 기억하는 순수 저장소이자, 고를 수 있는 캐릭터 8종의
고정 목록이다.

static/stage.js의 ABILITY_ROSTER와 id·label·emoji가 정확히 같아야 한다
(참가자가 고르지 않았을 때 쓰는 부서 기반 폴백과 동일한 시각 언어를
공유하기 위함). 하나를 바꾸면 반드시 다른 쪽도 함께 바꿀 것.
"""

from __future__ import annotations

CHARACTER_ROSTER: list[dict[str, str]] = [
    {"id": "nitro", "label": "니트로 부스트", "emoji": "🔥"},
    {"id": "shield", "label": "배리어 실드", "emoji": "🛡️"},
    {"id": "spark", "label": "스파크 러시", "emoji": "⚡"},
    {"id": "draft", "label": "슬립스트림", "emoji": "🌪️"},
    {"id": "lucky", "label": "럭키 드래프트", "emoji": "💎"},
    {"id": "wave", "label": "타이달 웨이브", "emoji": "🌊"},
    {"id": "stardust", "label": "스타더스트", "emoji": "⭐"},
    {"id": "rocket", "label": "로켓 대시", "emoji": "🚀"},
]

CHARACTER_IDS = {c["id"] for c in CHARACTER_ROSTER}
