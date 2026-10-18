"""Personalizer agent: decides what each viewer sees and how it is told.

An analyst gets tactical detail and numbers; a casual fan gets fewer, punchier
moments told from their club's point of view, in their language.
"""

from __future__ import annotations

import json

from matchmind.agents.interpreter import MatchState
from matchmind.i18n import LANGUAGES, label
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


CASUAL_LINES = {
    ("goal", 1): "Get in! {who} scores for {team}!",
    ("goal", -1): "Ouch. {who} scores for {team}.",
    ("goal", 0): "{who} scores for {team}!",
    ("big_chance", 1): "So close! {who} nearly scores for {team}.",
    ("big_chance", -1): "Phew, that was close. {who} nearly scored for {team}.",
    ("big_chance", 0): "Huge chance for {who}!",
    ("momentum_swing", 1): "{team} are on top right now. Keep it up!",
    ("momentum_swing", -1): "{team} are piling on the pressure. Hold on!",
    ("momentum_swing", 0): "{team} are on top right now.",
    ("card", 1): "{who} goes into the book.",
    ("card", -1): "{who} is booked. Careful now!",
    ("card", 0): "Yellow card for {who}.",
}


def rules_personalize(m: Moment, x: Explanation, n: Narration, p: Persona, s: MatchState, rel: float) -> PersonalizedNarrative:
    who, team = s.name(m.player_id), s.team_name(m.team_id)
    tag = label(m.type, p.language)
    if p.mode == "analyst":
        headline = f"{tag} · {x.headline}" if p.language != "en" else x.headline
        body = x.why
        if x.evidence:
            body += "  [" + " · ".join(x.evidence) + "]"
        if m.type in ("half_time", "full_time"):
            body = n.text + ("  [" + " · ".join(x.evidence) + "]" if x.evidence else "")
    else:
        sent = _sentiment(m, p)
        line = CASUAL_LINES.get((m.type, sent))
        headline = tag
        if m.type in ("half_time", "full_time"):
            body = n.text.split(". Standout")[0]
        elif line:
            body = line.format(who=who, team=team)
            if m.type == "goal":
                body += f" {m.evidence.get('score', s.scoreline())}."
        else:
            body = x.why.split(". ")[0].rstrip(".") + "."
    return PersonalizedNarrative(
        moment_id=m.id, persona_id=p.id, headline=headline, body=body,
        language=p.language if p.language == "en" else "en", relevance=rel, source="rules",
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
        out = await self.llm.complete_json(SYSTEM, json.dumps(payload, ensure_ascii=False, default=str), max_tokens=250)
        return PersonalizedNarrative(
            moment_id=m.id, persona_id=p.id,
            headline=str(out.get("headline") or base.headline)[:90],
            body=str(out.get("body") or base.body)[:600],
            language=p.language, relevance=rel, source="llm",
        )
