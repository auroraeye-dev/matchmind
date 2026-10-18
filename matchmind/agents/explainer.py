"""Explainer agent: says *why* a moment matters, citing numbers from state.

LLM path is grounded: the model only sees the moment's evidence and a compact
state snapshot, and must return the evidence it used. The rules path is a
fully deterministic fallback that is always available.
"""

from __future__ import annotations

import json

from matchmind.agents.interpreter import MatchState
from matchmind.llm.provider import ResilientLLM
from matchmind.schema import Explanation, Moment

SYSTEM = (
    "You are the Explainer agent in a football broadcast pipeline. Given one match moment and a stats "
    "snapshot from SYNTHETIC data, explain in plain language why the moment matters tactically or "
    "emotionally. Only use numbers present in the input. Respond as JSON: "
    '{"headline": str (<= 8 words), "why": str (1-2 sentences), "evidence": [str, ...] (2-4 short '
    'facts with numbers), "confidence": float 0-1}.'
)


def poss(name: str) -> str:
    """English possessive: "Rovers'" but "Harbor City FC's"."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _pct(v: float | None) -> str:
    return f"{v:.0f}%" if v is not None else "n/a"


def rules_explain(m: Moment, s: MatchState) -> Explanation:
    e = m.evidence
    who = s.name(m.player_id)
    team = s.team_name(m.team_id)
    opp = s.team_name(s.other(m.team_id)) if m.team_id else ""
    t = m.type

    if t == "goal":
        xg = e.get("xg", 0)
        quality = "a low-probability finish" if xg < 0.1 else "a well-worked chance" if xg < 0.3 else "a high-quality chance"
        assist = f" after {poss(s.name(e['assist_id']))} pass" if e.get("assist_id") else ""
        headline = f"GOAL! {who} scores for {team}"
        why = f"{who} finished from {e.get('distance_m', 0):.0f}m{assist}, {quality} ({xg:.2f} xG). {e.get('score', '')}."
        ev = [f"Shot xG {xg:.2f}", f"Distance {e.get('distance_m', 0):.0f}m",
              f"xG {team} {e.get('team_xg', 0):.2f} vs {opp} {e.get('opponent_xg', 0):.2f}"]
        if e.get("player_goals", 0) >= 2:
            ev.append(f"{poss(who)} goal #{e['player_goals']} today")
    elif t == "big_chance":
        outcome = {"saved": "kept out by the keeper", "off_target": "sent wide", "blocked": "blocked"}.get(e.get("outcome"), "missed")
        headline = f"Big chance for {team}"
        why = f"{poss(who)} {e.get('xg', 0):.2f} xG effort was {outcome}. {team} have now created {e.get('team_xg', 0):.2f} xG to {poss(opp)} {e.get('opponent_xg', 0):.2f}."
        ev = [f"Chance quality {e.get('xg', 0):.2f} xG", f"Distance {e.get('distance_m', 0):.0f}m",
              f"Shot speed {e.get('shot_speed_kmh', 0):.0f} km/h"]
    elif t == "momentum_swing":
        headline = f"Momentum swings to {team}"
        why = (f"In the last five minutes {team} have made {e.get('final_third_entries_5min', 0)} final-third entries "
               f"and {e.get('shots_5min', 0)} shots, with {_pct(e.get('possession_last_5min_pct'))} of the ball.")
        ev = [f"Momentum index {e.get('momentum', 0):+.2f}", f"{e.get('final_third_entries_5min', 0)} final-third entries (5 min)",
              f"Possession (5 min) {_pct(e.get('possession_last_5min_pct'))}"]
    elif t == "pressure_spike":
        headline = f"{poss(team)} press forces a turnover"
        why = f"{e.get('pressures_in_30s', 0)} pressing actions in 30 seconds won the ball back {e.get('turnover_zone', '')}."
        ev = [f"{e.get('pressures_in_30s', 0)} pressures in 30s", f"Turnover {e.get('turnover_zone', '')}",
              f"{e.get('team_pressures_total', 0)} pressures in the match"]
    elif t == "pass_combo":
        headline = f"{team} keep the ball"
        why = (f"{e.get('passes_in_sequence', 0)} passes in a row: {team} are controlling the tempo with "
               f"{_pct(e.get('possession_last_5min_pct'))} possession over the last five minutes.")
        ev = [f"{e.get('passes_in_sequence', 0)}-pass sequence", f"Possession (5 min) {_pct(e.get('possession_last_5min_pct'))}"]
    elif t == "great_pass":
        headline = f"Pass of the match contender from {who}"
        why = (f"{who} found {s.name(e.get('receiver_id'))} with a {e.get('pass_length_m', 0):.0f}m pass under "
               f"{100 * (e.get('pressure') or 0):.0f}% pressure.")
        ev = [f"Difficulty {e.get('difficulty', 0):.2f} / 1", f"Length {e.get('pass_length_m', 0):.0f}m",
              f"Ball speed {e.get('ball_speed_kmh', 0):.0f} km/h"]
    elif t == "chaos_phase":
        headline = "End-to-end chaos"
        why = (f"The ball is changing hands constantly, with neither side able to settle. Chaos index "
               f"{e.get('chaos_index', 0):.2f} and {e.get('events_per_min', 0):.0f} actions a minute.")
        ev = [f"Chaos index {e.get('chaos_index', 0):.2f}", f"{e.get('events_per_min', 0):.0f} events/min"]
    elif t == "control_phase":
        headline = f"{team} take control"
        why = f"{team} have had {_pct(e.get('possession_last_5min_pct'))} of the ball over five minutes and are dictating the rhythm."
        ev = [f"Possession (5 min) {_pct(e.get('possession_last_5min_pct'))}", f"Chaos index {e.get('chaos_index', 0):.2f}"]
    elif t == "milestone":
        headline = f"{who}: {e.get('value')} {e.get('stat')}"
        why = f"{who} reaches {e.get('value')} {e.get('stat')} and is at the heart of {poss(team)} play."
        ev = [f"{e.get('value')} {e.get('stat')}"]
    elif t == "card":
        headline = f"Yellow card for {who}"
        why = f"{who} is booked after {team} commit foul number {e.get('team_fouls', 0)}. Every challenge now carries risk."
        ev = [f"{team} fouls: {e.get('team_fouls', 0)}"]
    elif t == "substitution":
        off = s.name(e.get("player_off_id"))
        headline = f"{who} on for {off}"
        why = f"{team} make a change. {off} leaves after covering {e.get('off_distance_km', 0):.1f} km."
        ev = [f"{off}: {e.get('off_distance_km', 0):.1f} km covered", f"{off}: {e.get('off_passes', 0)} passes"]
    elif t in ("stat_check", "half_time", "full_time"):
        teams = e.get("teams", {})
        h, a = s.meta.home, s.meta.away
        th, ta = teams.get(h.id, {}), teams.get(a.id, {})
        label = {"stat_check": f"Stat check {m.clock}", "half_time": "Half-time", "full_time": "Full-time"}[t]
        headline = f"{label}: {e.get('score', s.scoreline())}" if t != "stat_check" else label
        leader = h.name if th.get("xg", 0) >= ta.get("xg", 0) else a.name
        why = f"{leader} have created the better chances{'' if t == 'full_time' else ' so far'}, with {max(th.get('xg', 0), ta.get('xg', 0)):.2f} xG."
        ev = [f"Possession {h.short} {_pct(th.get('possession_pct'))} / {a.short} {_pct(ta.get('possession_pct'))}",
              f"xG {h.short} {th.get('xg', 0):.2f} / {a.short} {ta.get('xg', 0):.2f}",
              f"Shots {h.short} {th.get('shots', 0)} / {a.short} {ta.get('shots', 0)}",
              f"Pass accuracy {h.short} {_pct(th.get('pass_accuracy_pct'))} / {a.short} {_pct(ta.get('pass_accuracy_pct'))}"]
    else:
        headline, why, ev = m.type.replace("_", " ").title(), "", []
    return Explanation(moment_id=m.id, headline=headline, why=why, evidence=ev, confidence=0.9, source="rules")


class Explainer:
    name = "explainer"

    def __init__(self, llm: ResilientLLM, min_importance: float):
        self.llm = llm
        self.min_importance = min_importance

    async def run(self, m: Moment, s: MatchState) -> Explanation:
        base = rules_explain(m, s)
        if m.importance < self.min_importance or m.type in ("stat_check",):
            return base
        payload = {
            "moment": {"type": m.type, "clock": m.clock, "team": s.team_name(m.team_id),
                       "player": s.name(m.player_id) if m.player_id else None, "evidence": _named(m, s)},
            "snapshot": s.snapshot(),
            "rules_draft": base.model_dump(include={"headline", "why", "evidence"}),
        }
        out = await self.llm.complete_json(SYSTEM, json.dumps(payload, default=str), agent="explainer")
        return Explanation(
            moment_id=m.id,
            headline=str(out.get("headline") or base.headline)[:90],
            why=str(out.get("why") or base.why)[:400],
            evidence=[str(x)[:80] for x in (out.get("evidence") or base.evidence)][:4],
            confidence=float(out.get("confidence", 0.75)),
            source="llm",
        )


def _named(m: Moment, s: MatchState) -> dict:
    """Swap player ids for names so the model never invents them."""
    out = {}
    for k, v in m.evidence.items():
        if k.endswith("_id") and isinstance(v, str):
            out[k[:-3]] = s.name(v)
        else:
            out[k] = v
    return out
