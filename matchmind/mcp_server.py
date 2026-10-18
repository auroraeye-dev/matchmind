"""MatchMind as an MCP server: match intelligence as tools for Copilot and other agents.

  stdio (VS Code / GitHub Copilot agent mode):  python -m matchmind.mcp_server
  streamable HTTP: mounted by the web app at /mcp
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from matchmind.agents.explainer import rules_explain
from matchmind.agents.interpreter import Interpreter, MatchState
from matchmind.agents.narrator import rules_narrate
from matchmind.agents.personalizer import rules_personalize, wants
from matchmind.schema import MatchEvent, MatchMeta, Moment, Persona
from matchmind.sim.engine import generate_match

mcp = MCPServer(
    name="matchmind",
    title="MatchMind match intelligence",
    instructions=(
        "Tools over SYNTHETIC football matches. Every match is identified by an integer seed. "
        "Use get_match_state to see the score and stats at any minute, list_moments to find key moments, "
        "explain_moment to get a grounded explanation, and personalize_moment to tailor it to a viewer."
    ),
    version="0.2.0",
)


def minute_of(clock: str) -> float:
    m = re.match(r"(\d+)(?:\+(\d+))?:(\d+)", clock)
    return int(m[1]) + int(m[2] or 0) + int(m[3]) / 60 if m else 0.0


@lru_cache(maxsize=16)
def _match(seed: int) -> tuple[MatchMeta, tuple[MatchEvent, ...]]:
    meta, events = generate_match(seed)
    return meta, tuple(events)


def _included(ev: MatchEvent, minute: float | None) -> bool:
    if minute is None:
        return True
    if ev.period == 1:
        return minute >= 45 or minute_of(ev.clock) <= minute
    return minute >= 45 and minute_of(ev.clock) <= minute


def _replay(seed: int, minute: float | None = None) -> tuple[MatchState, list[Moment]]:
    meta, events = _match(seed)
    it = Interpreter(meta)
    moments: list[Moment] = []
    for ev in events:
        if not _included(ev, minute):
            break
        moments += it.apply(ev)[1]
    return it.state, moments


def _find(seed: int, moment_id: str) -> tuple[MatchState, Moment]:
    state, moments = _replay(seed)
    for m in moments:
        if m.id == moment_id:
            return state, m
    raise ValueError(f"no moment {moment_id!r} in match seed {seed}")


@mcp.tool(description="Teams, line-ups, venue and final score for a synthetic match.")
def get_match(seed: int = 7) -> dict[str, Any]:
    meta, events = _match(seed)
    return {
        "match_id": meta.match_id, "competition": meta.competition, "venue": meta.venue, "weather": meta.weather,
        "home": {"id": meta.home.id, "name": meta.home.name, "players": [f"#{p.number} {p.name} ({p.position}) id={p.id}" for p in meta.home.players]},
        "away": {"id": meta.away.id, "name": meta.away.name, "players": [f"#{p.number} {p.name} ({p.position}) id={p.id}" for p in meta.away.players]},
        "final_score": events[-1].extra.get("score"), "events": len(events), "synthetic": True,
    }


@mcp.tool(description="Score, team stats, momentum and control-vs-chaos index at a given match minute (omit for full time).")
def get_match_state(seed: int = 7, minute: float | None = None) -> dict[str, Any]:
    state, _ = _replay(seed, minute)
    return state.snapshot()


@mcp.tool(description="Key moments (goals, big chances, momentum swings, pressing traps...) up to a minute, most important first.")
def list_moments(seed: int = 7, up_to_minute: float | None = None, min_importance: float = 0.45, limit: int = 20) -> list[dict[str, Any]]:
    state, moments = _replay(seed, up_to_minute)
    picked = sorted((m for m in moments if m.importance >= min_importance), key=lambda m: (-m.importance, m.t))[:limit]
    return [{"id": m.id, "clock": m.clock, "type": m.type, "importance": m.importance,
             "team": state.team_name(m.team_id), "player": state.name(m.player_id) if m.player_id else None} for m in picked]


@mcp.tool(description="Grounded explanation of why a moment matters, with the numbers that support it.")
def explain_moment(seed: int, moment_id: str) -> dict[str, Any]:
    state, m = _find(seed, moment_id)
    x = rules_explain(m, state)
    return {"moment": {"id": m.id, "clock": m.clock, "type": m.type}, **x.model_dump(include={"headline", "why", "evidence"})}


@mcp.tool(description="Tailor a moment for a viewer. mode: analyst|casual; language: en, es, fr, pt, hi, ar.")
def personalize_moment(seed: int, moment_id: str, mode: str = "casual", favorite_team_id: str | None = None,
                       focus_player_id: str | None = None, language: str = "en") -> dict[str, Any]:
    state, m = _find(seed, moment_id)
    p = Persona(id="mcp", mode=mode, favorite_team_id=favorite_team_id, focus_player_id=focus_player_id, language=language)
    show, rel = wants(m, p)
    x = rules_explain(m, state)
    pn = rules_personalize(m, x, rules_narrate(m, x, state), p, state, rel)
    return {"show_to_viewer": show, "relevance": rel, "headline": pn.headline, "body": pn.body}


@mcp.tool(description="Live player tag data: distance covered, top speed, passes, xG and goals up to a minute.")
def get_player_tag(seed: int, player_id: str, minute: float | None = None) -> dict[str, Any]:
    state, _ = _replay(seed, minute)
    p = state.player(player_id)
    if not p:
        raise ValueError(f"unknown player {player_id!r}")
    ps = state.players[player_id]
    return {"name": p.name, "number": p.number, "position": p.position, "team": state.team_name(p.team_id),
            "distance_km": ps.distance_km, "top_speed_kmh": round(ps.top_speed_kmh, 1), "touches": ps.touches,
            "passes": ps.passes, "passes_completed": ps.passes_completed, "shots": ps.shots, "xg": round(ps.xg, 2),
            "goals": ps.goals, "assists": ps.assists, "tackles_won": ps.tackles_won}


@mcp.tool(description="Raw synthetic events between two minutes, optionally filtered by type (pass, shot, goal, tackle...). Max 200.")
def get_events(seed: int = 7, from_minute: float = 0, to_minute: float = 95, event_type: str | None = None) -> list[dict[str, Any]]:
    _, events = _match(seed)
    out = [e.model_dump(mode="json", exclude_none=True) for e in events
           if from_minute <= minute_of(e.clock) <= to_minute and (event_type is None or e.type.value == event_type)]
    return out[:200]


@mcp.resource("matchmind://schema/event", description="JSON schema of a synthetic MatchEvent.")
def event_schema() -> dict[str, Any]:
    return MatchEvent.model_json_schema()


if __name__ == "__main__":
    mcp.run("stdio")
