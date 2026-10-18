"""Renderer agent: turns narratives into timed, machine-readable overlays."""

from __future__ import annotations

import itertools
import os

from matchmind.agents.interpreter import MatchState
from matchmind.schema import MatchEvent, Moment, Overlay, Persona, PersonalizedNarrative

_ids = itertools.count(1)

DISPLAY_MS = {"goal": 9000, "half_time": 15000, "full_time": 20000, "big_chance": 7000}
PRIORITY = {"goal": 5, "full_time": 5, "half_time": 5, "big_chance": 4, "card": 3, "momentum_swing": 3}
TAG_EVENTS = {"shot", "goal", "carry", "tackle", "pass"}


def _metric_rows(s: MatchState, metric: str) -> list[dict]:
    h, a = s.meta.home, s.meta.away
    th, ta = s.teams[h.id], s.teams[a.id]
    poss = s.possession_pct()
    rows = {
        "xg": [("xG", f"{th.xg:.2f}", f"{ta.xg:.2f}"), ("Shots", th.shots, ta.shots), ("On target", th.on_target, ta.on_target)],
        "possession": [("Possession", f"{poss[h.id]:.0f}%", f"{poss[a.id]:.0f}%"),
                       ("Last 5 min", f"{s.possession_5m[h.id]:.0f}%", f"{s.possession_5m[a.id]:.0f}%")],
        "pass_accuracy": [("Pass accuracy", f"{100 * th.pass_accuracy:.0f}%", f"{100 * ta.pass_accuracy:.0f}%"),
                          ("Passes", th.passes, ta.passes), ("Final-third entries", th.final_third_entries, ta.final_third_entries)],
        "pressure": [("Pressures", th.pressures, ta.pressures), ("Tackles won", th.tackles_won, ta.tackles_won),
                     ("Interceptions", th.interceptions, ta.interceptions)],
        "distance": [("Top speed (km/h)", f"{max((s.players[p.id].top_speed_kmh for p in h.players), default=0):.1f}",
                      f"{max((s.players[p.id].top_speed_kmh for p in a.players), default=0):.1f}"),
                     ("Distance (km)", f"{sum(s.players[p.id].distance_km for p in h.players):.1f}",
                      f"{sum(s.players[p.id].distance_km for p in a.players):.1f}")],
    }[metric]
    return [{"label": r[0], "home": r[1], "away": r[2]} for r in rows]


class Renderer:
    name = "renderer"

    def render(self, m: Moment, pn: PersonalizedNarrative, p: Persona, s: MatchState) -> list[Overlay]:
        kind = "recap" if m.type in ("half_time", "full_time") else "stat_card" if m.type == "stat_check" else "lower_third"
        data: dict = {"moment_type": m.type, "relevance": pn.relevance, "team_id": m.team_id,
                      "mode": p.mode, "favorite": bool(p.favorite_team_id and m.team_id == p.favorite_team_id)}
        if pn.evidence:
            data["evidence"] = pn.evidence
        if kind in ("stat_card", "recap") or (p.mode == "analyst" and "tactical" in m.tags):
            data["rows"] = _metric_rows(s, p.metric)
            data["metric"] = p.metric
        if kind == "recap":
            data["top_players"] = m.evidence.get("top_players", [])
        return [
            Overlay(
                id=f"ov{next(_ids):05d}",
                persona_id=p.id,
                kind=kind,
                t_match=m.t,
                clock=m.clock,
                display_ms=DISPLAY_MS.get(m.type, 6000),
                priority=PRIORITY.get(m.type, 2),
                title=pn.headline,
                body=pn.body,
                data=data,
                language=pn.language,
                moment_id=m.id,
                source=pn.source,
            )
        ]

    # Predefined thresholds that pop a player tag for every viewer (configurable via env).
    SPRINT_KMH = float(os.getenv("TAG_SPRINT_KMH", "25.2"))
    ROCKET_KMH = float(os.getenv("TAG_BALL_KMH", "95"))
    DISTANCE_MILESTONES_KM = tuple(float(x) for x in os.getenv("TAG_DISTANCE_KM", "3,6,9,11").split(","))

    def __init__(self) -> None:
        self._distance_done: dict[str, int] = {}

    def triggers(self, ev: MatchEvent) -> list[str]:
        """Threshold triggers for this event, independent of who is watching."""
        out: list[str] = []
        if ev.player_speed_kmh and ev.player_speed_kmh >= self.SPRINT_KMH and ev.type.value in ("carry", "pressure", "tackle"):
            out.append("sprint")
        if ev.ball_speed_kmh and ev.ball_speed_kmh >= self.ROCKET_KMH:
            out.append("rocket")
        if ev.type.value == "shot":
            out.append("shot")
        if ev.player_id and ev.distance_covered_km is not None:
            done = self._distance_done.get(ev.player_id, 0)
            marks = self.DISTANCE_MILESTONES_KM
            while done < len(marks) and ev.distance_covered_km >= marks[done]:
                done += 1
                if "distance" not in out:
                    out.append("distance")
            self._distance_done[ev.player_id] = done
        return out

    def player_tags(self, ev: MatchEvent, derived: dict, personas: list[Persona], s: MatchState) -> list[Overlay]:
        """Shared threshold tags (persona "*") plus a follow tag for each viewer following this player."""
        if not ev.player_id or ev.x is None or ev.player_id not in s.players:
            return []
        trig = self.triggers(ev)
        out: list[Overlay] = []
        if trig:
            out.append(self._tag(ev, derived, s, "*", trig, "en"))
        for p in personas:
            if p.focus_player_id == ev.player_id and ev.type.value in TAG_EVENTS:
                out.append(self._tag(ev, derived, s, p.id, ["follow", *trig], p.language))
        return out

    def _tag(self, ev: MatchEvent, derived: dict, s: MatchState, persona_id: str, trig: list[str], lang: str) -> Overlay:
        pl = s.player(ev.player_id)
        ps = s.players[ev.player_id]
        data = {
            "player_id": ev.player_id,
            "number": pl.number if pl else None,
            "team_id": ev.team_id,
            "trigger": trig,
            "speed_kmh": ev.player_speed_kmh,
            "distance_km": ps.distance_km,
            "top_speed_kmh": round(ps.top_speed_kmh, 1),
            "pass_accuracy": round(ps.passes_completed / ps.passes, 3) if ps.passes else None,
            "passes": ps.passes,
        }
        if ev.ball_speed_kmh:
            data["ball_speed_kmh"] = ev.ball_speed_kmh
        for k in ("pass_length_m", "pass_difficulty"):
            if k in derived:
                data[k] = derived[k]
        if ev.xg is not None:
            data["xg"] = ev.xg
        return Overlay(
            id=f"ov{next(_ids):05d}", persona_id=persona_id, kind="player_tag", t_match=ev.t, clock=ev.clock,
            display_ms=3000 if "follow" not in trig else 2500, priority=2 if persona_id == "*" else 1,
            title=pl.name if pl else ev.player_id, data=data, anchor={"x": ev.x, "y": ev.y}, language=lang,
        )
