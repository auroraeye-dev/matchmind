"""Deterministic synthetic football match engine.

Produces a football-realistic event stream (passes, carries, shots, tackles,
pressure, possession changes, set pieces, cards, substitutions) from a seed.
Same seed -> identical match, which makes demos and tests reproducible.

Coordinates: pitch is 105 x 68 m, x=0 is the home goal line. The home team
attacks +x in the first half and -x in the second half.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from matchmind.schema import EventType, MatchEvent, MatchMeta, Player, Team
from matchmind.sim.teams import build_team

L, W = 105.0, 68.0
GOAL_HALF = 3.66
HALF = 2700.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else min(v, hi)


def clock_str(t: float, period: int, period_start: float = 0.0) -> str:
    """Broadcast clock ("67:12", "45+2:05") from continuous match time."""
    base, cap = (0.0, 45) if period == 1 else (HALF, 90)
    total = base + max(0.0, t - period_start)
    minute, sec = int(total // 60), int(total % 60)
    if minute >= cap:
        return f"{cap}+{minute - cap + 1}:{sec:02d}"
    return f"{minute:02d}:{sec:02d}"


def shot_xg(x: float, y: float, attack_dir: int, pressure: float, header: bool) -> tuple[float, float]:
    """Simple angle/distance expected-goals model. Returns (xg, distance_m)."""
    gx = L if attack_dir == 1 else 0.0
    dx, dy = abs(gx - x), abs(34.0 - y)
    dist = math.hypot(dx, dy)
    # Angle subtended by the goal mouth.
    a1 = math.atan2(dy + GOAL_HALF, max(dx, 0.1))
    a2 = math.atan2(dy - GOAL_HALF, max(dx, 0.1))
    angle = abs(a1 - a2)
    logit = -1.6 + 2.2 * angle - 0.07 * dist - 0.6 * pressure - (0.7 if header else 0.0)
    return round(1 / (1 + math.exp(-logit)), 3), dist


class MatchSimulator:
    def __init__(self, seed: int = 7, home: Team | None = None, away: Team | None = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.home = home or build_team(0, seed)
        self.away = away or build_team(1, seed)
        self.match_id = f"SYN-{seed:05d}"
        self.teams = {self.home.id: self.home, self.away.id: self.away}
        self.on_pitch: dict[str, list[Player]] = {
            t.id: [p for p in t.players if p.starter] for t in (self.home, self.away)
        }
        self.bench: dict[str, list[Player]] = {
            t.id: [p for p in t.players if not p.starter and p.position != "GK"] for t in (self.home, self.away)
        }
        self.on_time: dict[str, float] = {p.id: 0.0 for t in (self.home, self.away) for p in t.players if p.starter}
        self.off_time: dict[str, float] = {}
        self.seq = 0
        self.t = 0.0
        self.period = 1
        self.score = {self.home.id: 0, self.away.id: 0}
        self.poss = self.home.id
        self.carrier: Player = self._by_pos(self.home.id, "ST")
        self.bx, self.by = L / 2, W / 2
        self.last_pass: tuple[str, float] | None = None  # (passer_id, t)
        self.period_start = 0.0
        self.subs_plan = self._plan_subs()

    # ------------------------------------------------------------------ metadata

    def meta(self) -> MatchMeta:
        r = random.Random(self.seed * 31)
        kickoff = datetime(2026, 10, 1, 14, 0, tzinfo=UTC) + timedelta(days=r.randint(0, 60))
        return MatchMeta(
            match_id=self.match_id,
            seed=self.seed,
            competition="MatchMind Synthetic League",
            venue=r.choice(["Ironworks Park", "Quayside Stadium", "Larkfield Arena", "The Meadows"]),
            kickoff_utc=kickoff.isoformat(),
            weather=r.choice(["clear, 14°C", "light rain, 9°C", "overcast, 11°C", "windy, 12°C"]),
            home=self.home,
            away=self.away,
        )

    # ------------------------------------------------------------------ helpers

    def _other(self, team_id: str) -> str:
        return self.away.id if team_id == self.home.id else self.home.id

    def _dir(self, team_id: str) -> int:
        d = 1 if team_id == self.home.id else -1
        return d if self.period == 1 else -d

    def _progress(self, team_id: str, x: float) -> float:
        return x / L if self._dir(team_id) == 1 else (L - x) / L

    def _by_pos(self, team_id: str, pos: str) -> Player:
        for p in self.on_pitch[team_id]:
            if p.position == pos:
                return p
        return self.on_pitch[team_id][-1]

    def _pos_of(self, p: Player) -> tuple[float, float]:
        """Approximate live position: base slot shifted with the ball, in absolute coords."""
        team = p.team_id
        prog = self._progress(team, self.bx)
        shift = (prog - 0.5) * 30.0 if p.position != "GK" else (prog - 0.5) * 6
        fx = _clamp(p.base_x + shift, 2.0, 103.0)
        fy = p.base_y + (self.by - 34.0) * 0.25
        x = fx if self._dir(team) == 1 else L - fx
        y = fy if self._dir(team) == 1 else W - fy
        return x, _clamp(y, 1.0, 67.0)

    def _nearest(self, team_id: str, x: float, y: float, exclude: str | None = None, outfield: bool = True) -> Player:
        best, best_d = None, 1e9
        for p in self.on_pitch[team_id]:
            if p.id == exclude or (outfield and p.position == "GK"):
                continue
            px, py = self._pos_of(p)
            d = (px - x) ** 2 + (py - y) ** 2 + self.rng.uniform(0, 40)
            if d < best_d:
                best, best_d = p, d
        return best  # type: ignore[return-value]

    def _distance_km(self, p: Player) -> float:
        on = self.on_time.get(p.id, self.t)
        end = self.off_time.get(p.id, self.t)
        rate = 0.05 if p.position == "GK" else 0.105 * (0.85 + 0.3 * p.stamina)  # km per minute
        return round(max(0.0, (end - on) / 60.0) * rate, 2)

    def _plan_subs(self) -> list[tuple[float, str]]:
        plan = []
        for team in (self.home.id, self.away.id):
            for _ in range(3):
                plan.append((self.rng.uniform(10 * 60, 37 * 60), team))  # seconds into the 2nd half
        return sorted(plan)

    def _emit(self, etype: EventType, **kw) -> MatchEvent:
        self.seq += 1
        player_id = kw.get("player_id")
        if player_id and "distance_covered_km" not in kw:
            p = self._player(player_id)
            if p:
                kw["distance_covered_km"] = self._distance_km(p)
        for k in ("x", "y", "end_x", "end_y", "pressure", "ball_speed_kmh", "player_speed_kmh"):
            if kw.get(k) is not None:
                kw[k] = round(kw[k], 2)
        return MatchEvent(
            id=f"{self.match_id}-{self.seq:05d}",
            seq=self.seq,
            match_id=self.match_id,
            t=round(self.t, 2),
            period=self.period,
            clock=clock_str(self.t, self.period, self.period_start),
            type=etype,
            **kw,
        )

    def _player(self, pid: str) -> Player | None:
        for t in (self.home, self.away):
            for p in t.players:
                if p.id == pid:
                    return p
        return None

    def _change_possession(self, new_team: str, carrier: Player, cause: str) -> MatchEvent:
        old = self.poss
        self.poss, self.carrier = new_team, carrier
        self.last_pass = None
        return self._emit(
            EventType.POSSESSION_CHANGE,
            team_id=new_team,
            player_id=carrier.id,
            x=self.bx,
            y=self.by,
            extra={"from_team": old, "cause": cause},
        )

    # ------------------------------------------------------------------ main loop

    def events(self) -> Iterator[MatchEvent]:
        for period in (1, 2):
            self.period = period
            start = 0.0 if period == 1 else self.t
            self.period_start = start
            end = start + HALF + self.rng.uniform(90, 300)
            self.t = start
            yield from self._kickoff(self.home.id if period == 1 else self.away.id)
            while self.t < end:
                yield from self._maybe_subs()
                yield from self._step()
            self.t = max(self.t, end)
            yield self._emit(
                EventType.HALF_TIME if period == 1 else EventType.FULL_TIME,
                extra={"score": dict(self.score)},
            )

    def _kickoff(self, team_id: str) -> Iterator[MatchEvent]:
        self.bx, self.by = L / 2, W / 2
        self.poss, self.carrier = team_id, self._by_pos(team_id, "ST")
        self.last_pass = None
        yield self._emit(EventType.KICKOFF, team_id=team_id, player_id=self.carrier.id, x=self.bx, y=self.by)
        self.t += 1.0

    def _maybe_subs(self) -> Iterator[MatchEvent]:
        while self.period == 2 and self.subs_plan and self.subs_plan[0][0] <= self.t - self.period_start:
            _, team = self.subs_plan.pop(0)
            if not self.bench[team]:
                continue
            on = self.bench[team].pop(0)
            candidates = [p for p in self.on_pitch[team] if p.position not in ("GK",) and p.id != self.carrier.id]
            same = [p for p in candidates if p.position[-1] == on.position[-1]] or candidates
            off = min(same, key=lambda p: p.stamina + self.rng.uniform(0, 0.3))
            idx = self.on_pitch[team].index(off)
            on = on.model_copy(update={"base_x": off.base_x, "base_y": off.base_y, "position": off.position})
            self.on_pitch[team][idx] = on
            self.off_time[off.id] = self.t
            self.on_time[on.id] = self.t
            yield self._emit(
                EventType.SUBSTITUTION,
                team_id=team,
                player_id=on.id,
                related_player_id=off.id,
                distance_covered_km=0.0,
                extra={"off_distance_km": self._distance_km(off)},
            )
            self.t += 25.0

    def _step(self) -> Iterator[MatchEvent]:
        rng = self.rng
        team, carrier = self.poss, self.carrier
        opp = self._other(team)
        p = self._progress(team, self.bx)
        pressure = _clamp(rng.betavariate(2, 6) + 0.28 * p, 0.0, 1.0)

        if pressure > 0.6 and rng.random() < 0.55:
            presser = self._nearest(opp, self.bx, self.by)
            yield self._emit(
                EventType.PRESSURE,
                team_id=opp,
                player_id=presser.id,
                related_player_id=carrier.id,
                x=self.bx,
                y=self.by,
                pressure=pressure,
                player_speed_kmh=rng.uniform(18, 30),
            )

        if rng.random() < 0.014 + 0.02 * pressure:
            yield from self._foul(opp, carrier)
            return

        central = 1 - abs(self.by - 34.0) / 34.0
        shot_p = 0.0
        if p > 0.74:
            shot_p = 0.02 + 0.13 * ((p - 0.74) / 0.26) * central + 0.03 * carrier.shooting
        elif p > 0.62:
            shot_p = 0.006 * carrier.shooting
        if rng.random() < shot_p:
            yield from self._shot(team, carrier, pressure, header=False)
            return

        if rng.random() < 0.2:
            yield from self._carry(team, carrier, pressure)
        else:
            yield from self._pass(team, carrier, pressure)

    # ------------------------------------------------------------------ actions

    def _pass(self, team: str, carrier: Player, pressure: float) -> Iterator[MatchEvent]:
        rng = self.rng
        d = self._dir(team)
        p = self._progress(team, self.bx)
        if carrier.position == "GK":
            length = rng.uniform(15, 60)
        else:
            length = _clamp(rng.lognormvariate(math.log(14), 0.55), 4, 60)
        angle = rng.gauss(0, 1.4 if p > 0.8 else 0.95)
        tx = _clamp(self.bx + math.cos(angle) * length * d, 1, L - 1)
        ty = _clamp(self.by + math.sin(angle) * length, 1, W - 1)
        length = math.hypot(tx - self.bx, ty - self.by)
        receiver = self._nearest(team, tx, ty, exclude=carrier.id)
        forward = (tx - self.bx) * d > 5
        success_p = 1.04 - 0.0065 * length - 0.28 * pressure + 0.3 * (carrier.passing - 0.72)
        if forward and p > 0.7:
            success_p -= 0.1
        speed = 25 + 1.6 * length + rng.gauss(0, 6)
        flight = length / max(speed / 3.6, 5)
        sx, sy = self.bx, self.by
        common = {
            "team_id": team,
            "player_id": carrier.id,
            "x": sx,
            "y": sy,
            "pressure": pressure,
            "ball_speed_kmh": speed,
            "player_speed_kmh": rng.uniform(4, 16),
        }

        if rng.random() < _clamp(success_p, 0.25, 0.98):
            yield self._emit(
                EventType.PASS, end_x=tx, end_y=ty, outcome="success", receiver_id=receiver.id, **common
            )
            self.bx, self.by, self.carrier = tx, ty, receiver
            self.last_pass = (carrier.id, self.t)
            self.t += flight + rng.uniform(1.2, 3.8)
            return

        r = rng.random()
        if r < 0.62:
            f = rng.uniform(0.4, 1.0)
            ix, iy = sx + (tx - sx) * f, sy + (ty - sy) * f
            yield self._emit(EventType.PASS, end_x=ix, end_y=iy, outcome="fail", receiver_id=receiver.id, **common)
            self.bx, self.by = ix, iy
            self.t += flight * f + 0.5
            opp = self._other(team)
            cutter = self._nearest(opp, ix, iy)
            yield self._emit(
                EventType.INTERCEPTION,
                team_id=opp,
                player_id=cutter.id,
                x=ix,
                y=iy,
                player_speed_kmh=rng.uniform(8, 24),
            )
            yield self._change_possession(opp, cutter, "interception")
            self.t += rng.uniform(1.0, 2.5)
        elif r < 0.86:
            yield self._emit(EventType.PASS, end_x=tx, end_y=ty, outcome="out", receiver_id=receiver.id, **common)
            yield from self._out_of_play(team, tx, ty)
        else:
            yield self._emit(EventType.PASS, end_x=tx, end_y=ty, outcome="fail", receiver_id=receiver.id, **common)
            self.bx, self.by = tx, ty
            self.t += flight + 1.0
            opp = self._other(team)
            yield self._change_possession(opp, self._nearest(opp, tx, ty), "loose_ball")
            self.t += rng.uniform(1.0, 2.0)

    def _out_of_play(self, team: str, x: float, y: float) -> Iterator[MatchEvent]:
        rng = self.rng
        opp = self._other(team)
        near_goal_line = self._progress(team, x) > 0.88
        if near_goal_line and rng.random() < 0.3:
            yield from self._corner(team)
            return
        if near_goal_line:
            yield from self._goal_kick(opp)
            return
        self.bx, self.by = _clamp(x, 1, L - 1), (0.5 if y < 34 else W - 0.5)
        thrower = self._nearest(opp, self.bx, self.by)
        self.t += rng.uniform(8, 18)
        yield self._change_possession(opp, thrower, "out_of_play")
        yield self._emit(EventType.THROW_IN, team_id=opp, player_id=thrower.id, x=self.bx, y=self.by)
        self.t += 1.5

    def _carry(self, team: str, carrier: Player, pressure: float) -> Iterator[MatchEvent]:
        rng = self.rng
        d = self._dir(team)
        dist = rng.uniform(4, 14) * (0.8 + 0.4 * carrier.pace)
        angle = rng.gauss(0, 0.6)
        tx = _clamp(self.bx + math.cos(angle) * dist * d, 1, L - 1)
        ty = _clamp(self.by + math.sin(angle) * dist, 1, W - 1)
        speed = 14 + 18 * carrier.pace * rng.uniform(0.6, 1.0)
        tackle_p = 0.1 + 0.25 * pressure - 0.1 * (carrier.pace - 0.7)
        if rng.random() < tackle_p:
            opp = self._other(team)
            tackler = self._nearest(opp, self.bx, self.by)
            mx, my = (self.bx + tx) / 2, (self.by + ty) / 2
            yield self._emit(
                EventType.CARRY,
                team_id=team,
                player_id=carrier.id,
                x=self.bx,
                y=self.by,
                end_x=mx,
                end_y=my,
                outcome="tackled",
                pressure=pressure,
                player_speed_kmh=speed,
            )
            self.bx, self.by = mx, my
            self.t += dist / 2 / (speed / 3.6)
            won = rng.random() < 0.55 + 0.3 * (tackler.defending - 0.6)
            yield self._emit(
                EventType.TACKLE,
                team_id=opp,
                player_id=tackler.id,
                related_player_id=carrier.id,
                x=mx,
                y=my,
                outcome="won" if won else "lost",
                player_speed_kmh=rng.uniform(10, 26),
            )
            if won:
                yield self._change_possession(opp, tackler, "tackle")
            self.t += rng.uniform(1.0, 2.0)
            return
        yield self._emit(
            EventType.CARRY,
            team_id=team,
            player_id=carrier.id,
            x=self.bx,
            y=self.by,
            end_x=tx,
            end_y=ty,
            outcome="success",
            pressure=pressure,
            player_speed_kmh=speed,
        )
        self.bx, self.by = tx, ty
        self.t += dist / (speed / 3.6) + rng.uniform(0.3, 1.2)

    def _shot(self, team: str, shooter: Player, pressure: float, header: bool) -> Iterator[MatchEvent]:
        rng = self.rng
        d = self._dir(team)
        opp = self._other(team)
        gx0 = L if d == 1 else 0.0
        if math.hypot(gx0 - self.bx, 34 - self.by) < 5:  # nobody shoots from on the goal line
            self.bx = gx0 - d * rng.uniform(5, 11)
            self.by = 34 + rng.uniform(-6, 6)
        xg, dist = shot_xg(self.bx, self.by, d, pressure, header)
        speed = (55 if header else 68) + 45 * shooter.shooting + rng.gauss(0, 8)
        r = rng.random()
        if r < xg * (0.8 + 0.4 * (shooter.shooting - 0.5)):
            outcome = "goal"
        else:
            r2 = rng.random()
            outcome = "blocked" if r2 < 0.25 else "off_target" if r2 < 0.62 else "saved"
        gx = L if d == 1 else 0.0
        ex = gx if outcome != "blocked" else self.bx + (gx - self.bx) * 0.2
        ey = 34 + rng.uniform(-3.2, 3.2) if outcome in ("goal", "saved") else 34 + rng.uniform(-9, 9)
        assist = None
        if self.last_pass and self.t - self.last_pass[1] < 12 and self.last_pass[0] != shooter.id:
            assist = self.last_pass[0]
        yield self._emit(
            EventType.SHOT,
            team_id=team,
            player_id=shooter.id,
            x=self.bx,
            y=self.by,
            end_x=ex,
            end_y=ey,
            outcome=outcome,
            xg=xg,
            pressure=pressure,
            ball_speed_kmh=speed,
            player_speed_kmh=rng.uniform(6, 22),
            body_part="head" if header else rng.choice(["right_foot", "right_foot", "left_foot"]),
            related_player_id=assist,
            extra={"distance_m": round(dist, 1)},
        )
        self.t += 1.0
        gk = self._by_pos(opp, "GK")
        if outcome == "goal":
            self.score[team] += 1
            yield self._emit(
                EventType.GOAL,
                team_id=team,
                player_id=shooter.id,
                related_player_id=assist,
                x=ex,
                y=ey,
                xg=xg,
                extra={"score": dict(self.score), "distance_m": round(dist, 1)},
            )
            self.t += rng.uniform(45, 75)
            yield from self._kickoff(opp)
        elif outcome == "saved":
            yield self._emit(EventType.SAVE, team_id=opp, player_id=gk.id, related_player_id=shooter.id, x=ex, y=ey, xg=xg)
            if rng.random() < 0.35:
                yield from self._corner(team)
            else:
                self.bx = 6.0 if self._dir(opp) == 1 else L - 6
                self.by = 34.0
                self.t += rng.uniform(6, 14)
                yield self._change_possession(opp, gk, "save")
        elif outcome == "blocked":
            if rng.random() < 0.45:
                yield from self._corner(team)
            else:
                self.bx = ex
                yield self._change_possession(opp, self._nearest(opp, self.bx, self.by), "block")
                self.t += 1.5
        else:
            yield from self._goal_kick(opp)

    def _goal_kick(self, team: str) -> Iterator[MatchEvent]:
        gk = self._by_pos(team, "GK")
        self.bx = 5.5 if self._dir(team) == 1 else L - 5.5
        self.by = 34.0
        self.t += self.rng.uniform(14, 28)
        if self.poss != team:
            yield self._change_possession(team, gk, "goal_kick")
        self.carrier = gk
        yield self._emit(EventType.GOAL_KICK, team_id=team, player_id=gk.id, x=self.bx, y=self.by)
        self.t += 1.0

    def _corner(self, team: str) -> Iterator[MatchEvent]:
        rng = self.rng
        d = self._dir(team)
        self.bx = L - 0.5 if d == 1 else 0.5
        self.by = 0.5 if rng.random() < 0.5 else W - 0.5
        taker = self._by_pos(team, rng.choice(["RW", "LW", "CM"]))
        self.t += rng.uniform(18, 32)
        if self.poss != team:
            yield self._change_possession(team, taker, "corner")
        self.carrier = taker
        yield self._emit(EventType.CORNER, team_id=team, player_id=taker.id, x=self.bx, y=self.by)
        tx = (L - rng.uniform(5, 12)) if d == 1 else rng.uniform(5, 12)
        ty = 34 + rng.uniform(-8, 8)
        target = self._nearest(team, tx, ty, exclude=taker.id)
        dist = math.hypot(tx - self.bx, ty - self.by)
        ok = rng.random() < 0.38
        yield self._emit(
            EventType.PASS,
            team_id=team,
            player_id=taker.id,
            x=self.bx,
            y=self.by,
            end_x=tx,
            end_y=ty,
            outcome="success" if ok else "fail",
            receiver_id=target.id,
            ball_speed_kmh=55 + rng.uniform(-8, 10),
            extra={"set_piece": "corner", "cross": True},
        )
        self.t += dist / 15 + 0.5
        self.bx, self.by = tx, ty
        if ok:
            self.carrier = target
            self.last_pass = (taker.id, self.t)
            yield from self._shot(team, target, rng.uniform(0.4, 0.9), header=rng.random() < 0.7)
        else:
            opp = self._other(team)
            yield self._change_possession(opp, self._nearest(opp, tx, ty), "clearance")
            self.t += 2.0

    def _foul(self, fouler_team: str, victim: Player) -> Iterator[MatchEvent]:
        rng = self.rng
        fouler = self._nearest(fouler_team, self.bx, self.by)
        yield self._emit(
            EventType.FOUL,
            team_id=fouler_team,
            player_id=fouler.id,
            related_player_id=victim.id,
            x=self.bx,
            y=self.by,
        )
        if rng.random() < 0.17:
            yield self._emit(
                EventType.CARD,
                team_id=fouler_team,
                player_id=fouler.id,
                outcome="yellow",
                x=self.bx,
                y=self.by,
            )
        self.t += rng.uniform(15, 35)
        self.last_pass = None


def generate_match(seed: int = 7) -> tuple[MatchMeta, list[MatchEvent]]:
    sim = MatchSimulator(seed)
    meta = sim.meta()
    return meta, list(sim.events())
