"""Shared data contracts for every stage of the pipeline.

Events flow:  MatchEvent -> (Interpreter) Moment -> (Explainer) Explanation
              -> (Narrator) Narration -> (Personalizer) PersonalizedNarrative
              -> (Renderer) Overlay
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class EventType(StrEnum):
    KICKOFF = "kickoff"
    PASS = "pass"
    CARRY = "carry"
    SHOT = "shot"
    GOAL = "goal"
    SAVE = "save"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    PRESSURE = "pressure"
    FOUL = "foul"
    CARD = "card"
    CORNER = "corner"
    THROW_IN = "throw_in"
    GOAL_KICK = "goal_kick"
    SUBSTITUTION = "substitution"
    POSSESSION_CHANGE = "possession_change"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"


# --------------------------------------------------------------------------- teams


class Player(BaseModel):
    id: str
    name: str
    number: int
    position: str
    team_id: str
    # Base position in an "attacking to the right" frame (own goal at x=0).
    base_x: float
    base_y: float
    passing: float
    shooting: float
    pace: float
    defending: float
    stamina: float
    starter: bool = True


class Team(BaseModel):
    id: str
    name: str
    short: str
    color: str
    players: list[Player]


class MatchMeta(BaseModel):
    schema_version: str = SCHEMA_VERSION
    synthetic: bool = True
    match_id: str
    seed: int
    competition: str
    venue: str
    kickoff_utc: str
    weather: str
    home: Team
    away: Team


# --------------------------------------------------------------------------- events


class MatchEvent(BaseModel):
    """One synthetic, football-realistic event. Pitch is 105 x 68 metres."""

    id: str
    seq: int
    match_id: str
    t: float = Field(description="Continuous match seconds incl. stoppage time; see clock for broadcast time")
    period: int
    clock: str
    type: EventType
    team_id: str | None = None
    player_id: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    receiver_id: str | None = None
    related_player_id: str | None = None
    pressure: float | None = None
    ball_speed_kmh: float | None = None
    player_speed_kmh: float | None = None
    distance_covered_km: float | None = None
    xg: float | None = None
    body_part: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- personas


class Persona(BaseModel):
    id: str = "fan"
    label: str = "Casual fan"
    mode: Literal["analyst", "casual"] = "casual"
    favorite_team_id: str | None = None
    focus_player_id: str | None = None
    language: str = "en"
    metric: Literal["xg", "possession", "pass_accuracy", "pressure", "distance"] = "xg"


# --------------------------------------------------------------------------- pipeline artefacts


class Moment(BaseModel):
    """Something the Interpreter thinks is worth talking about."""

    id: str
    type: str
    t: float
    clock: str
    period: int
    importance: float
    team_id: str | None = None
    player_id: str | None = None
    event_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class Explanation(BaseModel):
    moment_id: str
    headline: str
    why: str
    evidence: list[str]
    confidence: float = 0.8
    source: Literal["llm", "rules"] = "rules"


class Narration(BaseModel):
    moment_id: str
    text: str
    source: Literal["llm", "rules"] = "rules"


class PersonalizedNarrative(BaseModel):
    moment_id: str
    persona_id: str
    headline: str
    body: str
    language: str
    relevance: float
    evidence: list[str] = Field(default_factory=list)
    source: Literal["llm", "rules"] = "rules"


class Overlay(BaseModel):
    """Machine-readable, timed graphic for a broadcast/stream renderer."""

    id: str
    persona_id: str  # "*" = for every viewer
    kind: Literal["lower_third", "stat_card", "player_tag", "recap", "ticker"]
    t_match: float
    clock: str
    display_ms: int
    priority: int
    title: str
    body: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    anchor: dict[str, float] | None = None
    language: str = "en"
    moment_id: str | None = None
    source: Literal["llm", "rules"] = "rules"


class TraceRecord(BaseModel):
    agent: str
    moment_id: str | None
    persona_id: str | None = None
    status: Literal["ok", "fallback", "skipped", "error"]
    source: Literal["llm", "rules", "none"] = "rules"
    ms: float
    note: str = ""
