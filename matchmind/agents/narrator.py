"""Narrator agent: turns explanations into broadcast commentary and recaps."""

from __future__ import annotations

import json

from matchmind.agents.interpreter import MatchState
from matchmind.llm.provider import ResilientLLM
from matchmind.schema import Explanation, Moment, Narration

SYSTEM = (
    "You are the Narrator agent, a broadcast football commentator working from SYNTHETIC match data. "
    "Write vivid but factual commentary using only facts in the input. For recaps, write 3-5 sentences "
    'covering the result, the turning points and standout players. Respond as JSON: {"text": str}.'
)

LLM_TYPES = {"goal", "half_time", "full_time", "big_chance"}


def rules_narrate(m: Moment, x: Explanation, s: MatchState) -> Narration:
    if m.type in ("half_time", "full_time"):
        e = m.evidence
        label = "Half-time" if m.type == "half_time" else "Full-time"
        goals = e.get("goals", [])
        top = e.get("top_players", [])
        star = top[0] if top else None
        parts = [f"{label}: {e.get('score', s.scoreline())}."]
        if goals:
            parts.append("Goals: " + "; ".join(goals) + ".")
        parts.append(x.why)
        if star:
            parts.append(
                f"Standout: {star['name']} ({star['goals']} goal{'s' if star['goals'] != 1 else ''}, "
                f"{star['assists']} assist{'s' if star['assists'] != 1 else ''}, {star['xg']:.2f} xG)."
            )
        return Narration(moment_id=m.id, text=" ".join(parts), source="rules")
    return Narration(moment_id=m.id, text=f"{x.headline}. {x.why}", source="rules")


class Narrator:
    name = "narrator"

    def __init__(self, llm: ResilientLLM):
        self.llm = llm

    async def run(self, m: Moment, x: Explanation, s: MatchState) -> Narration:
        base = rules_narrate(m, x, s)
        if m.type not in LLM_TYPES:
            return base
        payload = {
            "moment_type": m.type,
            "clock": m.clock,
            "explanation": x.model_dump(include={"headline", "why", "evidence"}),
            "evidence": m.evidence if m.type in ("half_time", "full_time") else {},
            "snapshot": s.snapshot(),
        }
        out = await self.llm.complete_json(SYSTEM, json.dumps(payload, default=str), max_tokens=400)
        text = str(out.get("text") or "").strip()
        return Narration(moment_id=m.id, text=text[:900], source="llm") if text else base
