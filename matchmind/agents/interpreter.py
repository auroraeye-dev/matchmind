"""Interpreter agent: turns raw events into stats, patterns and Moments.

Deterministic and fast (no LLM) so it can keep up with a live feed. Owns the
shared MatchState that the downstream agents read from.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from matchmind.schema import EventType, MatchEvent, MatchMeta, Moment, Player

WINDOW_S = 300.0  # rolling 5-minute window for momentum / control / chaos
L = 105.0


@dataclass
class TeamStats:
    goals: int = 0
    shots: int = 0
    on_target: int = 0
    xg: float = 0.0
    passes: int = 0
    passes_completed: int = 0
    pass_distance_m: float = 0.0
    final_third_entries: int = 0
    tackles_won: int = 0
    interceptions: int = 0
    pressures: int = 0
    fouls: int = 0
    yellow_cards: int = 0
    corners: int = 0
    possession_s: float = 0.0
    fastest_shot_kmh: float = 0.0
    top_sprint_kmh: float = 0.0
    hardest_pass_difficulty: float = 0.0

    @property
    def pass_accuracy(self) -> float:
        return self.passes_completed / self.passes if self.passes else 0.0

    @property
    def defensive_actions(self) -> int:
        return self.tackles_won + self.interceptions + self.fouls


@dataclass
class PlayerStats:
    touches: int = 0
    passes: int = 0
    passes_completed: int = 0
    shots: int = 0
    goals: int = 0
    assists: int = 0
    xg: float = 0.0
    tackles_won: int = 0
    pressures: int = 0
    top_speed_kmh: float = 0.0
    distance_km: float = 0.0
    best_pass_difficulty: float = 0.0


def pass_difficulty(length: float, pressure: float, progression: float) -> float:
    """0..1 rating: long, pressured, forward passes are harder."""
    return round(
        min(1.0, 0.45 * min(length / 45.0, 1.0) + 0.35 * pressure + 0.2 * max(0.0, min(progression / 30.0, 1.0))),
        2,
    )


@dataclass
class MatchState:
    meta: MatchMeta
    t: float = 0.0
    clock: str = "00:00"
    period: int = 1
    possession_team: str | None = None
    score: dict[str, int] = field(default_factory=dict)
    teams: dict[str, TeamStats] = field(default_factory=dict)
    players: dict[str, PlayerStats] = field(default_factory=dict)
    momentum: float = 0.0  # -1 (away) .. +1 (home)
    chaos: float = 0.0  # 0 controlled .. 1 chaotic
    rhythm_epm: float = 0.0  # events per minute in window
    possession_5m: dict[str, float] = field(default_factory=dict)
    moments: list[Moment] = field(default_factory=list)

    def player(self, pid: str | None) -> Player | None:
        if not pid:
            return None
        for team in (self.meta.home, self.meta.away):
            for p in team.players:
                if p.id == pid:
                    return p
        return None

    def name(self, pid: str | None) -> str:
        p = self.player(pid)
        return p.name if p else "Unknown"

    def team_name(self, tid: str | None) -> str:
        if tid == self.meta.home.id:
            return self.meta.home.name
        if tid == self.meta.away.id:
            return self.meta.away.name
        return ""

    def other(self, tid: str) -> str:
        return self.meta.away.id if tid == self.meta.home.id else self.meta.home.id

    def possession_pct(self) -> dict[str, float]:
        total = sum(ts.possession_s for ts in self.teams.values()) or 1.0
        return {tid: round(100 * ts.possession_s / total, 1) for tid, ts in self.teams.items()}

    def ppda(self, tid: str) -> float | None:
        """Passes allowed per defensive action: opponent passes / our tackles+interceptions+fouls. Lower = harder press."""
        da = self.teams[tid].defensive_actions
        return round(self.teams[self.other(tid)].passes / da, 1) if da else None

    def scoreline(self) -> str:
        h, a = self.meta.home, self.meta.away
        return f"{h.name} {self.score[h.id]}-{self.score[a.id]} {a.name}"

    def snapshot(self) -> dict[str, Any]:
        """Compact, grounded context handed to LLM-backed agents."""
        poss = self.possession_pct()
        out: dict[str, Any] = {
            "clock": self.clock,
            "period": self.period,
            "score": self.scoreline(),
            "momentum_home_minus_away": round(self.momentum, 2),
            "chaos_index": round(self.chaos, 2),
            "possession_last_5min_pct": {self.team_name(k): v for k, v in self.possession_5m.items()},
            "teams": {},
        }
        for tid, ts in self.teams.items():
            out["teams"][self.team_name(tid)] = {
                "goals": ts.goals,
                "shots": ts.shots,
                "on_target": ts.on_target,
                "xg": round(ts.xg, 2),
                "pass_accuracy_pct": round(100 * ts.pass_accuracy, 1),
                "possession_pct": poss.get(tid, 50.0),
                "pressures": ts.pressures,
                "tackles_won": ts.tackles_won,
                "ppda": self.ppda(tid),
                "fastest_shot_kmh": round(ts.fastest_shot_kmh, 1),
            }
        out["rhythm_events_per_min"] = round(self.rhythm_epm, 1)
        return out

    def public_stats(self) -> dict[str, Any]:
        poss = self.possession_pct()
        return {
            "score": self.score,
            "clock": self.clock,
            "period": self.period,
            "momentum": round(self.momentum, 3),
            "chaos": round(self.chaos, 3),
            "rhythm_epm": round(self.rhythm_epm, 1),
            "possession_pct": poss,
            "possession_5m": self.possession_5m,
            "teams": {
                tid: {**asdict(ts), "xg": round(ts.xg, 2), "pass_accuracy": round(ts.pass_accuracy, 3), "ppda": self.ppda(tid)}
                for tid, ts in self.teams.items()
            },
        }


class Interpreter:
    name = "interpreter"

    def __init__(self, meta: MatchMeta):
        self.state = MatchState(meta=meta)
        s = self.state
        s.score = {meta.home.id: 0, meta.away.id: 0}
        s.teams = {meta.home.id: TeamStats(), meta.away.id: TeamStats()}
        s.players = {p.id: PlayerStats() for t in (meta.home, meta.away) for p in t.players}
        s.possession_5m = {meta.home.id: 50.0, meta.away.id: 50.0}
        self._window: deque[tuple[float, str, str, float]] = deque()  # (t, team, kind, value)
        self._last_t = 0.0
        self._pass_streak: tuple[str | None, int] = (None, 0)
        self._press_burst: deque[tuple[float, str]] = deque()
        self._last_flag_t: dict[str, float] = {}
        self._momentum_side = 0
        self._milestones: set[str] = set()
        self._next_stat_check = 600.0
        self._rhythm_base = 0.0  # slow-moving baseline of events/min
        self._n = 0

    # ------------------------------------------------------------------ public

    def apply(self, ev: MatchEvent) -> tuple[dict[str, Any], list[Moment]]:
        """Ingest one event. Returns (derived metrics for this event, new moments)."""
        s = self.state
        dt = max(0.0, ev.t - self._last_t)
        if s.possession_team and ev.type not in (EventType.HALF_TIME,):
            s.teams[s.possession_team].possession_s += dt
            self._window.append((ev.t, s.possession_team, "poss", dt))
        self._last_t = ev.t
        s.t, s.clock, s.period = ev.t, ev.clock, ev.period
        if ev.type in (EventType.KICKOFF, EventType.POSSESSION_CHANGE, EventType.GOAL_KICK, EventType.THROW_IN):
            s.possession_team = ev.team_id
        if ev.type == EventType.KICKOFF and ev.period == 2:
            self._momentum_side = 0

        derived: dict[str, Any] = {}
        moments: list[Moment] = []
        if ev.player_id and ev.player_id in s.players:
            ps = s.players[ev.player_id]
            ps.touches += 1
            if ev.player_speed_kmh:
                ps.top_speed_kmh = max(ps.top_speed_kmh, ev.player_speed_kmh)
                if ev.team_id in s.teams and ev.type.value in ("carry", "pressure", "tackle", "interception"):
                    s.teams[ev.team_id].top_sprint_kmh = max(s.teams[ev.team_id].top_sprint_kmh, ev.player_speed_kmh)
            if ev.distance_covered_km is not None:
                ps.distance_km = ev.distance_covered_km

        handler = getattr(self, f"_on_{ev.type.value}", None)
        if handler:
            handler(ev, derived, moments)

        self._roll_window(ev.t)
        moments += self._detect_patterns(ev)
        for m in moments:
            s.moments.append(m)
        return derived, moments

    # ------------------------------------------------------------------ handlers

    def _dir(self, team_id: str, period: int) -> int:
        d = 1 if team_id == self.state.meta.home.id else -1
        return d if period == 1 else -d

    def _on_pass(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        s = self.state
        ts, ps = s.teams[ev.team_id], s.players[ev.player_id]
        length = math.hypot((ev.end_x or 0) - (ev.x or 0), (ev.end_y or 0) - (ev.y or 0))
        d = self._dir(ev.team_id, ev.period)
        progression = ((ev.end_x or 0) - (ev.x or 0)) * d
        diff = pass_difficulty(length, ev.pressure or 0.0, progression)
        derived.update(pass_length_m=round(length, 1), pass_difficulty=diff, progression_m=round(progression, 1))
        ts.passes += 1
        ps.passes += 1
        if ev.outcome == "success":
            ts.passes_completed += 1
            ps.passes_completed += 1
            ts.pass_distance_m += length
            ps.best_pass_difficulty = max(ps.best_pass_difficulty, diff)
            ts.hardest_pass_difficulty = max(ts.hardest_pass_difficulty, diff)
            start_prog = (ev.x if d == 1 else L - ev.x) / L
            end_prog = (ev.end_x if d == 1 else L - ev.end_x) / L
            threat = max(0.0, end_prog - start_prog) + (0.08 if end_prog > 0.66 else 0.0)
            self._window.append((ev.t, ev.team_id, "threat", threat))
            if start_prog <= 0.66 < end_prog:
                ts.final_third_entries += 1
                self._window.append((ev.t, ev.team_id, "f3", 1))
            team, n = self._pass_streak
            self._pass_streak = (ev.team_id, n + 1 if team == ev.team_id else 1)
            if self._pass_streak[1] in (12, 20, 30):
                moments.append(
                    self._moment(
                        "pass_combo",
                        ev,
                        0.45 + 0.05 * (self._pass_streak[1] // 10),
                        ["tactical", "control"],
                        passes_in_sequence=self._pass_streak[1],
                        possession_last_5min_pct=s.possession_5m.get(ev.team_id),
                    )
                )
            if diff >= 0.68 and length >= 32 and self._cooldown("great_pass", ev.t, 240):
                moments.append(
                    self._moment(
                        "great_pass",
                        ev,
                        0.4 + 0.3 * (diff - 0.68),
                        ["player", "skill"],
                        receiver_id=ev.receiver_id,
                        pass_length_m=round(length, 1),
                        pressure=ev.pressure,
                        difficulty=diff,
                        ball_speed_kmh=ev.ball_speed_kmh,
                    )
                )
        else:
            self._pass_streak = (None, 0)
        self._milestone(ev, moments, "passes", ps.passes, (50, 75, 100))

    def _on_carry(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        d = self._dir(ev.team_id, ev.period)
        prog = ((ev.end_x or 0) - (ev.x or 0)) * d
        derived.update(carry_m=round(prog, 1))
        if ev.outcome == "success":
            self._window.append((ev.t, ev.team_id, "threat", max(0.0, prog) / L))

    def _on_shot(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        s = self.state
        ts, ps = s.teams[ev.team_id], s.players[ev.player_id]
        ts.shots += 1
        ps.shots += 1
        ts.xg += ev.xg or 0
        ps.xg += ev.xg or 0
        ts.fastest_shot_kmh = max(ts.fastest_shot_kmh, ev.ball_speed_kmh or 0)
        if ev.outcome in ("goal", "saved"):
            ts.on_target += 1
        self._window.append((ev.t, ev.team_id, "threat", 3 * (ev.xg or 0)))
        self._window.append((ev.t, ev.team_id, "shot", 1))
        self._pass_streak = (None, 0)
        derived.update(shot_distance_m=ev.extra.get("distance_m"))
        if ev.outcome != "goal" and (ev.xg or 0) >= 0.22:
            moments.append(
                self._moment(
                    "big_chance",
                    ev,
                    0.55 + min(0.3, ev.xg or 0),
                    ["drama", "chance"],
                    xg=ev.xg,
                    outcome=ev.outcome,
                    distance_m=ev.extra.get("distance_m"),
                    body_part=ev.body_part,
                    team_xg=round(ts.xg, 2),
                    opponent_xg=round(s.teams[s.other(ev.team_id)].xg, 2),
                    shot_speed_kmh=ev.ball_speed_kmh,
                )
            )

    def _on_goal(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        s = self.state
        s.score[ev.team_id] += 1
        s.teams[ev.team_id].goals += 1
        s.players[ev.player_id].goals += 1
        if ev.related_player_id and ev.related_player_id in s.players:
            s.players[ev.related_player_id].assists += 1
        ts, os_ = s.teams[ev.team_id], s.teams[s.other(ev.team_id)]
        moments.append(
            self._moment(
                "goal",
                ev,
                1.0,
                ["drama", "goal"],
                xg=ev.xg,
                distance_m=ev.extra.get("distance_m"),
                assist_id=ev.related_player_id,
                score=s.scoreline(),
                team_xg=round(ts.xg, 2),
                opponent_xg=round(os_.xg, 2),
                player_goals=s.players[ev.player_id].goals,
                momentum=round(s.momentum, 2),
            )
        )

    def _on_tackle(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        if ev.outcome == "won":
            self.state.teams[ev.team_id].tackles_won += 1
            self.state.players[ev.player_id].tackles_won += 1

    def _on_interception(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        self.state.teams[ev.team_id].interceptions += 1

    def _on_pressure(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        self.state.teams[ev.team_id].pressures += 1
        self.state.players[ev.player_id].pressures += 1
        self._press_burst.append((ev.t, ev.team_id))
        self._window.append((ev.t, ev.team_id, "press", 1))

    def _on_possession_change(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        s = self.state
        self._window.append((ev.t, ev.team_id, "turnover", 1))
        self._pass_streak = (None, 0)
        while self._press_burst and ev.t - self._press_burst[0][0] > 30:
            self._press_burst.popleft()
        n = sum(1 for _, team in self._press_burst if team == ev.team_id)
        if n >= 2 and ev.extra.get("cause") in ("interception", "tackle") and self._cooldown("press", ev.t, 240):
            d = self._dir(ev.team_id, ev.period)
            prog = (ev.x if d == 1 else L - ev.x) / L
            zone = "in the attacking third" if prog > 0.66 else "in midfield" if prog > 0.33 else "deep"
            moments.append(
                self._moment(
                    "pressure_spike",
                    ev,
                    0.45 + (0.1 if prog > 0.66 else 0.0),
                    ["tactical", "pressing"],
                    pressures_in_30s=n,
                    turnover_zone=zone,
                    team_pressures_total=s.teams[ev.team_id].pressures,
                )
            )

    def _on_foul(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        self.state.teams[ev.team_id].fouls += 1

    def _on_card(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        s = self.state
        s.teams[ev.team_id].yellow_cards += 1
        moments.append(
            self._moment(
                "card", ev, 0.5, ["discipline"], card=ev.outcome, team_fouls=s.teams[ev.team_id].fouls
            )
        )

    def _on_corner(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        self.state.teams[ev.team_id].corners += 1

    def _on_substitution(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        s = self.state
        off = ev.related_player_id
        moments.append(
            self._moment(
                "substitution",
                ev,
                0.3,
                ["squad"],
                player_off_id=off,
                off_distance_km=ev.extra.get("off_distance_km"),
                off_passes=s.players[off].passes if off in s.players else None,
            )
        )

    def _on_half_time(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        moments.append(self._recap_moment(ev, "half_time"))

    def _on_full_time(self, ev: MatchEvent, derived: dict, moments: list) -> None:
        moments.append(self._recap_moment(ev, "full_time"))

    # ------------------------------------------------------------------ patterns

    def _roll_window(self, now: float) -> None:
        s = self.state
        while self._window and now - self._window[0][0] > WINDOW_S:
            self._window.popleft()
        home, away = s.meta.home.id, s.meta.away.id
        threat = {home: 0.0, away: 0.0}
        poss = {home: 0.0, away: 0.0}
        turnovers = 0
        for _, team, kind, v in self._window:
            if kind == "threat":
                threat[team] += v
            elif kind == "poss":
                poss[team] += v
            elif kind == "turnover":
                turnovers += 1
        raw = (threat[home] - threat[away]) / (threat[home] + threat[away] + 0.6)
        s.momentum = 0.7 * s.momentum + 0.3 * raw
        span_min = max(1.0, min(WINDOW_S, now - (self._window[0][0] if self._window else now)) / 60.0)
        s.chaos = max(0.0, min(1.0, (turnovers / span_min - 1.2) / 2.5))
        s.rhythm_epm = len(self._window) / span_min
        total = poss[home] + poss[away]
        if total > 60:
            s.possession_5m = {home: round(100 * poss[home] / total, 1), away: round(100 * poss[away] / total, 1)}

    def _detect_patterns(self, ev: MatchEvent) -> list[Moment]:
        s = self.state
        out: list[Moment] = []
        if ev.t < 360:  # let the window warm up
            return out
        home, away = s.meta.home.id, s.meta.away.id
        side = 1 if s.momentum > 0.35 else -1 if s.momentum < -0.35 else 0
        if side and side != self._momentum_side and self._cooldown("momentum", ev.t, 300):
            team = home if side == 1 else away
            f3 = sum(1 for _, t, k, _ in self._window if k == "f3" and t == team)
            shots = sum(1 for _, t, k, _ in self._window if k == "shot" and t == team)
            out.append(
                self._moment(
                    "momentum_swing",
                    ev,
                    0.55,
                    ["tactical", "momentum"],
                    team_id=team,
                    momentum=round(s.momentum, 2),
                    final_third_entries_5min=f3,
                    shots_5min=shots,
                    possession_last_5min_pct=s.possession_5m.get(team),
                )
            )
            self._momentum_side = side
        elif not side and abs(s.momentum) < 0.1:
            self._momentum_side = 0

        self._rhythm_base = s.rhythm_epm if not self._rhythm_base else 0.995 * self._rhythm_base + 0.005 * s.rhythm_epm
        ratio = s.rhythm_epm / self._rhythm_base if self._rhythm_base else 1.0
        if ev.t > 900 and (ratio > 1.3 or ratio < 0.72) and self._cooldown("tempo", ev.t, 900):
            out.append(
                self._moment(
                    "tempo_shift",
                    ev,
                    0.42,
                    ["tactical", "rhythm"],
                    team_id=None,
                    direction="up" if ratio > 1 else "down",
                    events_per_min=round(s.rhythm_epm, 1),
                    baseline_events_per_min=round(self._rhythm_base, 1),
                    change_pct=round(100 * (ratio - 1)),
                )
            )
        if s.chaos > 0.6 and self._cooldown("chaos", ev.t, 600):
            out.append(
                self._moment(
                    "chaos_phase",
                    ev,
                    0.4,
                    ["tactical", "chaos"],
                    team_id=None,
                    chaos_index=round(s.chaos, 2),
                    events_per_min=round(s.rhythm_epm, 1),
                )
            )
        for team in (home, away):
            if s.possession_5m.get(team, 0) >= 68 and self._cooldown(f"control-{team}", ev.t, 600):
                out.append(
                    self._moment(
                        "control_phase",
                        ev,
                        0.4,
                        ["tactical", "control"],
                        team_id=team,
                        possession_last_5min_pct=s.possession_5m[team],
                        chaos_index=round(s.chaos, 2),
                    )
                )
        if ev.t >= self._next_stat_check and ev.type not in (EventType.HALF_TIME, EventType.FULL_TIME):
            self._next_stat_check += 900.0
            out.append(self._moment("stat_check", ev, 0.32, ["stat"], team_id=None, **self._stat_evidence()))
        return out

    # ------------------------------------------------------------------ helpers

    def _cooldown(self, key: str, t: float, gap: float) -> bool:
        last = self._last_flag_t.get(key, -1e9)
        if t - last >= gap:
            self._last_flag_t[key] = t
            return True
        return False

    def _milestone(self, ev: MatchEvent, moments: list, stat: str, value: int, marks: tuple[int, ...]) -> None:
        key = f"{ev.player_id}-{stat}-{value}"
        if value in marks and key not in self._milestones:
            self._milestones.add(key)
            moments.append(
                self._moment("milestone", ev, 0.33 + 0.02 * (value // 25), ["player", "stat"], stat=stat, value=value)
            )

    def _stat_evidence(self) -> dict[str, Any]:
        s = self.state
        poss = s.possession_pct()
        ev: dict[str, Any] = {}
        for tid in (s.meta.home.id, s.meta.away.id):
            ts = s.teams[tid]
            ev[tid] = {
                "possession_pct": poss.get(tid),
                "xg": round(ts.xg, 2),
                "shots": ts.shots,
                "pass_accuracy_pct": round(100 * ts.pass_accuracy, 1),
                "pressures": ts.pressures,
            }
        return {"teams": ev}

    def _recap_moment(self, ev: MatchEvent, kind: str) -> Moment:
        s = self.state
        key = sorted((m for m in s.moments if m.type in ("goal", "big_chance", "momentum_swing", "card")), key=lambda m: m.t)
        top = sorted(
            ((pid, ps) for pid, ps in s.players.items() if ps.touches),
            key=lambda kv: kv[1].goals * 3 + kv[1].assists * 2 + kv[1].xg + kv[1].passes_completed / 40 + kv[1].tackles_won / 4,
            reverse=True,
        )[:3]
        return self._moment(
            kind,
            ev,
            1.0,
            ["recap"],
            team_id=None,
            score=s.scoreline(),
            goals=[f"{m.clock} {s.name(m.player_id)} ({s.meta.home.short if m.team_id == s.meta.home.id else s.meta.away.short})" for m in key if m.type == "goal"],
            key_moments=[f"{m.clock} {m.type} {s.name(m.player_id) if m.player_id else s.team_name(m.team_id)}" for m in key if m.type != "goal"][-8:],
            top_players=[
                {"name": s.name(pid), "goals": ps.goals, "assists": ps.assists, "xg": round(ps.xg, 2), "passes": ps.passes}
                for pid, ps in top
            ],
            **self._stat_evidence(),
        )

    def _moment(self, mtype: str, ev: MatchEvent, importance: float, tags: list[str], **evidence: Any) -> Moment:
        self._n += 1
        team_id = evidence.pop("team_id", ev.team_id)
        return Moment(
            id=f"m{self._n:04d}",
            type=mtype,
            t=ev.t,
            clock=ev.clock,
            period=ev.period,
            importance=round(min(1.0, importance), 2),
            team_id=team_id,
            player_id=ev.player_id if mtype not in ("momentum_swing", "chaos_phase", "control_phase", "tempo_shift", "stat_check", "half_time", "full_time") else None,
            event_id=ev.id,
            tags=tags,
            evidence={k: v for k, v in evidence.items() if v is not None},
        )
