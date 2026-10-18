"""Vision agent: auto-eventing from a (synthetic) tracking feed.

Watches ball frames and who controls the ball, and reconstructs on-ball
events with no event data:

  controlled by A -> in flight -> controlled by teammate B       => pass (success)
  controlled by A -> in flight -> controlled by opponent C       => pass (fail) + interception + possession change
  controlled by A -> controlled by opponent C (no flight)        => tackle won + possession change
  fast flight towards goal -> crosses the line between the posts => shot + goal
  fast flight towards goal -> keeper takes it at the line        => shot (saved) + save
  fast flight towards goal -> over / wide                        => shot (off target)

Official events (cards, subs, restarts, period markers) pass straight through.
Detected events carry extra={"detected": True, "source": "vision"}.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from matchmind.schema import EventType, MatchEvent, MatchMeta
from matchmind.sim.engine import clock_str, shot_xg
from matchmind.sim.tracking import Frame

MODEL_PATH = Path(__file__).with_name("vision_model.json")
FEATURES = ["bias", "peak_speed", "start_goal_dist", "end_goal_dist", "directness", "start_xg", "flight_length",
            "max_height", "to_teammate", "to_opp_keeper", "to_opp_outfield", "over_goal_line", "over_touchline"]
GOAL_HALF, BAR = 3.66, 2.44
SHOT_KMH = 48.0
LINE_EPS = 0.4  # metres past the line before we believe it (tracking noise is ~0.15 m)


class VisionAutoEventer:
    name = "vision"

    def __init__(self, meta: MatchMeta, model: dict[str, Any] | bool | None = True, collect: list | None = None):
        """model=True loads the trained shot classifier if present; None/False uses hand-written rules."""
        self.meta = meta
        self.model = load_model() if model is True else (model or None)
        self.collect = collect  # when a list, every flight's features are appended (for training)
        self.home = meta.home.id
        self.gk = {p.id for t in (meta.home, meta.away) for p in t.players if p.position == "GK"}
        self.period, self.period_start = 1, 0.0
        self.seq = 0
        self.cur: tuple[str, str] | None = None
        self.last_team: str | None = None
        self.flight: dict[str, Any] | None = None
        self.prev: Frame | None = None
        self.dead = False
        self.detected = 0
        self.last_t = 0.0  # keep the output stream monotonic in time

    # ------------------------------------------------------------------ helpers

    def _dir(self, team: str) -> int:
        d = 1 if team == self.home else -1
        return d if self.period == 1 else -d

    def _ev(self, etype: EventType, t: float, **kw: Any) -> MatchEvent:
        t = self.last_t = max(t, self.last_t)
        self.seq += 1
        self.detected += 1
        extra = {"detected": True, "source": "vision", **kw.pop("extra", {})}
        for k in ("x", "y", "end_x", "end_y", "ball_speed_kmh"):
            if kw.get(k) is not None:
                kw[k] = round(kw[k], 2)
        return MatchEvent(id=f"{self.meta.match_id}-V{self.seq:05d}", seq=self.seq, match_id=self.meta.match_id,
                          t=round(t, 2), period=self.period, clock=clock_str(t, self.period, self.period_start),
                          type=etype, extra=extra, **kw)

    def _possession(self, t: float, who: tuple[str, str], x: float, y: float, cause: str) -> MatchEvent:
        old, self.last_team = self.last_team, who[1]
        return self._ev(EventType.POSSESSION_CHANGE, t, team_id=who[1], player_id=who[0], x=x, y=y,
                        extra={"from_team": old, "cause": cause})

    # ------------------------------------------------------------------ inputs

    def official(self, ev: MatchEvent) -> list[MatchEvent]:
        out: list[MatchEvent] = []
        if self.flight is not None and ev.type in (EventType.THROW_IN, EventType.GOAL_KICK, EventType.CORNER):
            # the referee says the ball went out: close the open flight as a pass out of play
            fl, p = self.flight, self.prev
            out.append(self._ev(EventType.PASS, fl["t0"], team_id=fl["frm"][1], player_id=fl["frm"][0], x=fl["x0"], y=fl["y0"],
                                end_x=p.x if p else None, end_y=p.y if p else None, outcome="out", ball_speed_kmh=fl["peak"]))
        if ev.type in (EventType.KICKOFF, EventType.HALF_TIME, EventType.FULL_TIME, EventType.THROW_IN,
                       EventType.GOAL_KICK, EventType.CORNER):
            self.flight = None
        if ev.type == EventType.KICKOFF:
            self.period = ev.period
            if ev.period == 2 and self.period_start == 0.0:
                self.period_start = ev.t
            self.dead, self.flight = False, None
        if ev.type in (EventType.KICKOFF, EventType.THROW_IN, EventType.GOAL_KICK, EventType.CORNER):
            self.last_team = ev.team_id
            self.cur = (ev.player_id, ev.team_id) if ev.player_id else None
            self.flight, self.dead = None, False
        self.seq += 1
        self.last_t = max(self.last_t, ev.t)
        out.append(ev.model_copy(update={"seq": self.seq, "extra": {**ev.extra, "source": "officials"}}))
        return out

    def frame(self, f: Frame) -> list[MatchEvent]:
        out: list[MatchEvent] = []
        prev, self.prev = self.prev, f
        if prev is None:
            return out
        dt = max(f.t - prev.t, 1e-3)
        speed = math.hypot(f.x - prev.x, f.y - prev.y) / dt * 3.6

        if f.possessor_id is None:
            if self.dead:
                return out
            if self.flight is None and self.cur is not None:
                self.flight = {"t0": prev.t, "x0": prev.x, "y0": prev.y, "frm": self.cur, "peak": 0.0, "zmax": 0.0}
                self.cur = None
            if self.flight is not None:
                if speed < 200:  # ignore teleports across dropped frames
                    self.flight["peak"] = max(self.flight["peak"], speed)
                self.flight["zmax"] = max(self.flight["zmax"], f.z)
                out += self._check_line(f)
            return out

        who = (f.possessor_id, f.possessor_team)
        self.dead = False
        if self.flight is not None:
            out += self._land(f, who)
        elif self.cur is not None and who != self.cur:
            if who[1] != self.cur[1]:
                out.append(self._ev(EventType.TACKLE, f.t, team_id=who[1], player_id=who[0], related_player_id=self.cur[0],
                                    x=f.x, y=f.y, outcome="won"))
                out.append(self._possession(f.t, who, f.x, f.y, "tackle"))
            else:
                out.append(self._ev(EventType.PASS, f.t, team_id=who[1], player_id=self.cur[0], receiver_id=who[0],
                                    x=prev.x, y=prev.y, end_x=f.x, end_y=f.y, outcome="success", ball_speed_kmh=speed))
        elif self.cur is None and self.last_team and who[1] != self.last_team:
            out.append(self._possession(f.t, who, f.x, f.y, "loose_ball"))
        self.cur = who
        self.last_team = who[1]
        return out

    # ------------------------------------------------------------------ flight endings

    def _towards_goal(self, fl: dict[str, Any], x: float, y: float) -> bool:
        """Ball ends closer to the centre of the attacked goal than it started, from within 40 m."""
        gx = 105.0 if self._dir(fl["frm"][1]) == 1 else 0.0
        start = math.hypot(gx - fl["x0"], 34.0 - fl["y0"])
        return math.hypot(gx - x, 34.0 - y) < start and start < 40

    def _goal_dist(self, fl: dict[str, Any]) -> float:
        gx = 105.0 if self._dir(fl["frm"][1]) == 1 else 0.0
        return math.hypot(gx - fl["x0"], 34.0 - fl["y0"])

    def features(self, fl: dict[str, Any], ex: float, ey: float, landing: str) -> list[float]:
        d = self._dir(fl["frm"][1])
        gx = 105.0 if d == 1 else 0.0
        s0 = math.hypot(gx - fl["x0"], 34.0 - fl["y0"])
        s1 = math.hypot(gx - ex, 34.0 - ey)
        length = math.hypot(ex - fl["x0"], ey - fl["y0"])
        xg0 = shot_xg(fl["x0"], fl["y0"], d, 0.0, False)[0]
        return [1.0, fl["peak"] / 100, min(s0, 80) / 40, min(s1, 80) / 40, (s0 - s1) / max(length, 1.0), xg0 * 4,
                min(length, 80) / 40, fl.get("zmax", 0.0) / 3,
                float(landing == "teammate"), float(landing == "gk_opp"), float(landing == "opp"),
                float(landing == "line"), float(landing == "side")]

    def _is_shot(self, fl: dict[str, Any], ex: float, ey: float, landing: str, rule: bool) -> bool:
        x = self.features(fl, ex, ey, landing)
        if self.collect is not None:
            self.collect.append({"t0": fl["t0"], "player_id": fl["frm"][0], "x": x})
        if not self.model:
            return rule
        z = sum(w * v for w, v in zip(self.model["weights"], x, strict=True))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z)))) >= self.model.get("threshold", 0.5)

    def _shot(self, fl: dict[str, Any], t: float, ex: float, ey: float, outcome: str) -> MatchEvent:
        pid, team = fl["frm"]
        xg, dist = shot_xg(fl["x0"], fl["y0"], self._dir(team), 0.3, False)
        return self._ev(EventType.SHOT, fl["t0"], team_id=team, player_id=pid, x=fl["x0"], y=fl["y0"], end_x=ex, end_y=ey,
                        outcome=outcome, xg=xg, ball_speed_kmh=fl["peak"], extra={"distance_m": round(dist, 1)})

    def _check_line(self, f: Frame) -> list[MatchEvent]:
        fl = self.flight
        out: list[MatchEvent] = []
        over_line = f.x > 105.0 + LINE_EPS or f.x < -LINE_EPS
        off_side = f.y < -LINE_EPS or f.y > 68.0 + LINE_EPS
        if not (over_line or off_side):
            return out
        pid, team = fl["frm"]
        towards = self._towards_goal(fl, f.x, f.y)
        rule = over_line and fl["peak"] >= SHOT_KMH and towards
        shooting = over_line and self._is_shot(fl, f.x, f.y, "line" if over_line else "side", rule)
        in_goal = over_line and towards and abs(f.y - 34.0) < GOAL_HALF and f.z < BAR and fl["peak"] >= 30
        if in_goal:  # the ball is in the net: physics, not a classifier, decides goals
            out.append(self._shot(fl, f.t, f.x, f.y, "goal"))
            out.append(self._ev(EventType.GOAL, f.t, team_id=team, player_id=pid, x=f.x, y=f.y, xg=out[0].xg,
                                extra={"distance_m": out[0].extra.get("distance_m")}))
        elif shooting:
            out.append(self._shot(fl, f.t, f.x, f.y, "off_target"))
        else:
            out.append(self._ev(EventType.PASS, fl["t0"], team_id=team, player_id=pid, x=fl["x0"], y=fl["y0"],
                                end_x=f.x, end_y=f.y, outcome="out", ball_speed_kmh=fl["peak"]))
        self.flight, self.dead = None, True
        return out

    def _land(self, f: Frame, who: tuple[str, str]) -> list[MatchEvent]:
        fl, self.flight = self.flight, None
        frm = fl["frm"]
        out: list[MatchEvent] = []
        if who == frm:
            return out  # possessor flicker or a touch-and-go dribble
        near_line = f.x > 102.0 or f.x < 3.0
        ok = who[1] == frm[1]
        landing = "teammate" if ok else "gk_opp" if who[0] in self.gk else "opp"
        towards = self._towards_goal(fl, f.x, f.y)
        rule = (landing == "gk_opp" and near_line and fl["peak"] >= SHOT_KMH and towards) or (
            landing == "opp" and fl["peak"] >= 62 and towards and self._goal_dist(fl) < 28)
        shot = self._is_shot(fl, f.x, f.y, landing, rule) and not ok
        if landing == "gk_opp" and not near_line:
            shot = False  # a keeper claiming a cross or a long ball away from the line is not a save
        if shot and landing == "gk_opp":
            out.append(self._shot(fl, f.t, f.x, f.y, "saved"))
            out.append(self._ev(EventType.SAVE, f.t, team_id=who[1], player_id=who[0], related_player_id=frm[0], x=f.x, y=f.y,
                                xg=out[0].xg))
            out.append(self._possession(f.t, who, f.x, f.y, "save"))
            return out
        if shot:
            # A hard strike at goal that an outfield opponent stops: a blocked shot.
            out.append(self._shot(fl, f.t, f.x, f.y, "blocked"))
            out.append(self._possession(f.t, who, f.x, f.y, "block"))
            return out
        out.append(self._ev(EventType.PASS, fl["t0"], team_id=frm[1], player_id=frm[0], receiver_id=who[0] if ok else None,
                            x=fl["x0"], y=fl["y0"], end_x=f.x, end_y=f.y, outcome="success" if ok else "fail",
                            ball_speed_kmh=fl["peak"]))
        if not ok:
            out.append(self._ev(EventType.INTERCEPTION, f.t, team_id=who[1], player_id=who[0], x=f.x, y=f.y))
            out.append(self._possession(f.t, who, f.x, f.y, "interception"))
        return out


def load_model(path: Path = MODEL_PATH) -> dict[str, Any] | None:
    try:
        model = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return model if model.get("features") == FEATURES else None


def auto_event_stream(meta: MatchMeta, feed, model: dict[str, Any] | bool | None = True) -> Any:
    """Turn a tracking + officials feed into a MatchEvent stream."""
    vision = VisionAutoEventer(meta, model=model)
    for kind, item in feed:
        yield from (vision.official(item) if kind == "official" else vision.frame(item))


def evaluate(truth: list[MatchEvent], detected: list[MatchEvent]) -> dict[str, Any]:
    """Count-level and timing-matched recall/precision for the detected event types."""
    report: dict[str, Any] = {}
    for etype in ("pass", "shot", "goal", "interception", "save"):
        tr = [e for e in truth if e.type.value == etype]
        if etype == "interception":  # vision can't tell an interception from a loose-ball recovery
            tr += [e for e in truth if e.type.value == "possession_change" and e.extra.get("cause") in ("loose_ball", "block")]
        de = [e for e in detected if e.type.value == etype and e.extra.get("source") == "vision"]
        used: set[int] = set()
        hits = 0
        for e in tr:
            for i, d in enumerate(de):
                if i not in used and abs(d.t - e.t) <= 1.5 and d.player_id == e.player_id:
                    used.add(i)
                    hits += 1
                    break
        report[etype] = {"truth": len(tr), "detected": len(de), "matched": hits,
                         "recall": round(hits / len(tr), 3) if tr else None,
                         "precision": round(hits / len(de), 3) if de else None}
    pt = [e for e in truth if e.type.value == "pass"]
    pd = [e for e in detected if e.type.value == "pass" and e.extra.get("source") == "vision"]
    acc = lambda es: sum(e.outcome == "success" for e in es) / len(es) if es else 0
    report["pass_accuracy"] = {"truth": round(acc(pt), 3), "detected": round(acc(pd), 3)}
    report["score"] = {"truth": truth[-1].extra.get("score"),
                       "detected": _score(detected)}
    return report


def _score(events: list[MatchEvent]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in events:
        if e.type == EventType.GOAL:
            out[e.team_id] = out.get(e.team_id, 0) + 1
    return out
