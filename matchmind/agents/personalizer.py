"""Personalizer agent: decides what each viewer sees and how it is told.

An analyst gets tactical detail and numbers; a casual fan gets fewer, punchier
moments told from their club's point of view, in their language.
"""

from __future__ import annotations

import json

from matchmind.agents.interpreter import MatchState
from matchmind.i18n import LANGUAGES, casual_line, label
from matchmind.llm.guardrails import enforce
from matchmind.llm.provider import ResilientLLM
from matchmind.schema import Explanation, Moment, Narration, Persona, PersonalizedNarrative

THRESHOLD = {"analyst": 0.38, "casual": 0.48}
ALWAYS = {"goal", "half_time", "full_time"}

SYSTEM = (
    "You are the Personalizer agent for a football broadcast built on SYNTHETIC data. Rewrite the given "
    "commentary for one viewer persona. Analysts want tactical insight and the key numbers. Casual fans "
    "want short, emotional, jargon-free lines, told from their favourite club's point of view if they have "
    "one. Write in the requested language. Never invent facts. Respond as JSON: "
    '{"headline": str (<= 7 words), "body": str (<= 2 sentences)}.'
)


def involved(m: Moment, player_id: str | None) -> bool:
    if not player_id:
        return False
    if m.player_id == player_id:
        return True
    return any(v == player_id for k, v in m.evidence.items() if k.endswith("_id"))


def relevance(m: Moment, p: Persona) -> float:
    r = m.importance
    if p.favorite_team_id and m.team_id:
        r += 0.12 if m.team_id == p.favorite_team_id else -0.05
    if involved(m, p.focus_player_id):
        r += 0.35
    tags = set(m.tags)
    if p.mode == "analyst":
        r += 0.15 if tags & {"tactical", "skill"} else 0.0
        r += 0.08 if "stat" in tags else 0.0
    else:
        r += 0.1 if "drama" in tags else 0.0
        r -= 0.12 if tags & {"stat", "squad"} else 0.0
        r -= 0.05 if "tactical" in tags else 0.0
    return round(max(0.0, min(1.0, r)), 2)


def wants(m: Moment, p: Persona) -> tuple[bool, float]:
    r = relevance(m, p)
    return (m.type in ALWAYS or r >= THRESHOLD[p.mode]), r


def _sentiment(m: Moment, p: Persona) -> int:
    if not p.favorite_team_id or not m.team_id:
        return 0
    good = m.type in {"goal", "big_chance", "momentum_swing", "pressure_spike", "pass_combo", "great_pass", "control_phase"}
    bad_for_them = m.type == "card"
    ours = m.team_id == p.favorite_team_id
    if good:
        return 1 if ours else -1
    if bad_for_them:
        return -1 if ours else 1
    return 0


def _goal_list(m: Moment) -> str:
    return ", ".join(m.evidence.get("goals", []))


def rules_personalize(m: Moment, x: Explanation, n: Narration, p: Persona, s: MatchState, rel: float) -> PersonalizedNarrative:
    who = s.name(m.player_id) if m.player_id else s.team_name(m.team_id)
    team = s.team_name(m.team_id) if m.team_id else ""
    score = m.evidence.get("score", s.scoreline())
    tag = label(m.type, p.language)
    star = "⭐ " if involved(m, p.focus_player_id) else ""
    if p.mode == "analyst":
        # Analysts get the full reasoning; numbers travel separately as evidence chips.
        headline = star + (f"{tag} · {x.headline}" if p.language != "en" else x.headline)
        body = n.text if m.type in ("half_time", "full_time") else x.why
        language = "en"  # full analyst prose in other languages needs Foundry or Azure AI Translator
    else:
        sent = _sentiment(m, p)
        body, language = casual_line(m.type, sent, p.language, who=who, team=team, score=score)
        if m.type in ("half_time", "full_time") and m.evidence.get("goals"):
            body += f" ⚽ {_goal_list(m)}"
        if not body:
            body, language = x.why.split(". ")[0].rstrip(".") + ".", "en"
        headline = star + tag
    return PersonalizedNarrative(
        moment_id=m.id, persona_id=p.id, headline=headline, body=body, evidence=list(x.evidence) if p.mode == "analyst" else [],
        language=language, relevance=rel, source="rules",
    )


class Personalizer:
    name = "personalizer"

    def __init__(self, llm: ResilientLLM, min_importance: float):
        self.llm = llm
        self.min_importance = min_importance

    async def run(self, m: Moment, x: Explanation, n: Narration, p: Persona, s: MatchState, rel: float) -> PersonalizedNarrative:
        base = rules_personalize(m, x, n, p, s, rel)
        # English analyst text is already good from rules; spend LLM calls where they add most.
        needs_llm = p.language != "en" or (p.mode == "casual" and m.importance >= self.min_importance) or m.type in ALWAYS
        if not needs_llm or m.importance < min(self.min_importance, 0.45):
            return base
        persona = {
            "mode": p.mode,
            "favorite_team": s.team_name(p.favorite_team_id) if p.favorite_team_id else None,
            "focus_player": s.name(p.focus_player_id) if p.focus_player_id else None,
            "language": LANGUAGES.get(p.language, p.language),
            "favourite_metric": p.metric,
        }
        payload = {"persona": persona, "moment_type": m.type, "clock": m.clock,
                   "commentary": n.text, "explanation": x.model_dump(include={"headline", "why", "evidence"})}
        out = enforce(await self.llm.complete_json(SYSTEM, json.dumps(payload, ensure_ascii=False, default=str), max_tokens=250, agent="personalizer"), payload)
        return PersonalizedNarrative(
            moment_id=m.id, persona_id=p.id,
            headline=str(out.get("headline") or base.headline)[:90],
            body=str(out.get("body") or base.body)[:600], evidence=base.evidence,
            language=p.language, relevance=rel, source="llm",
        )
