from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Participant:
    id: str
    name: str
    team: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Participant":
        return cls(id=data["id"], name=data["name"], team=data.get("team", ""))


@dataclass
class RoundPlan:
    round_index: int
    pass_count: int
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoundPlan":
        return cls(
            round_index=data["round_index"],
            pass_count=data["pass_count"],
            duration_seconds=data["duration_seconds"],
        )


@dataclass
class DrawResult:
    seed: str
    commit: str
    snapshot: dict[str, Any] = field(default_factory=dict)
    winners: list[str] = field(default_factory=list)
    ranking: list[str] = field(default_factory=list)
    round_pass_ids: dict[int, list[str]] = field(default_factory=dict)
    department_pass_rate: dict[int, dict[str, float]] = field(default_factory=dict)
    finalist_count: int = 0
    revealed: bool = False
    revealed_rounds: list[int] = field(default_factory=list)
    created_at: str = ""
    revealed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "commit": self.commit,
            "snapshot": self.snapshot,
            "winners": list(self.winners),
            "ranking": list(self.ranking),
            "round_pass_ids": {str(k): v for k, v in self.round_pass_ids.items()},
            "department_pass_rate": {
                str(k): v for k, v in self.department_pass_rate.items()
            },
            "finalist_count": self.finalist_count,
            "revealed": self.revealed,
            "revealed_rounds": list(self.revealed_rounds),
            "created_at": self.created_at,
            "revealed_at": self.revealed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DrawResult":
        return cls(
            seed=data["seed"],
            commit=data["commit"],
            snapshot=data.get("snapshot", {}),
            winners=list(data.get("winners", [])),
            ranking=list(data.get("ranking", [])),
            round_pass_ids={int(k): v for k, v in data.get("round_pass_ids", {}).items()},
            department_pass_rate={
                int(k): v for k, v in data.get("department_pass_rate", {}).items()
            },
            finalist_count=data.get("finalist_count", 0),
            revealed=data.get("revealed", False),
            revealed_rounds=list(data.get("revealed_rounds", [])),
            created_at=data.get("created_at", ""),
            revealed_at=data.get("revealed_at"),
        )


@dataclass
class Session:
    session_id: str
    participants: list[Participant] = field(default_factory=list)
    draw_count: int = 1
    excluded_ids: list[str] = field(default_factory=list)
    mode: str = "roulette"
    total_seconds: float = 600.0
    predictions_enabled: bool = False
    # "confidence"(무손실 확신도 배분) | "gambling"(승인된 사이버머니 갬블링).
    # predictions_enabled가 False면 어느 쪽도 쓰이지 않는다. 두 모드는 동시에
    # 참가자에게 강요하지 않는다 -- 세션당 하나만 활성화된다.
    prediction_mode: str = "confidence"
    created_at: str = ""
    draws: list[DrawResult] = field(default_factory=list)
    round_plans: list[RoundPlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "participants": [p.to_dict() for p in self.participants],
            "draw_count": self.draw_count,
            "excluded_ids": list(self.excluded_ids),
            "mode": self.mode,
            "total_seconds": self.total_seconds,
            "predictions_enabled": self.predictions_enabled,
            "prediction_mode": self.prediction_mode,
            "created_at": self.created_at,
            "draws": [d.to_dict() for d in self.draws],
            "round_plans": [r.to_dict() for r in self.round_plans],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            session_id=data["session_id"],
            participants=[Participant.from_dict(p) for p in data.get("participants", [])],
            draw_count=data.get("draw_count", 1),
            excluded_ids=list(data.get("excluded_ids", [])),
            mode=data.get("mode", "roulette"),
            total_seconds=data.get("total_seconds", 600.0),
            predictions_enabled=data.get("predictions_enabled", False),
            prediction_mode=data.get("prediction_mode", "confidence"),
            created_at=data.get("created_at", ""),
            draws=[DrawResult.from_dict(d) for d in data.get("draws", [])],
            round_plans=[RoundPlan.from_dict(r) for r in data.get("round_plans", [])],
        )
