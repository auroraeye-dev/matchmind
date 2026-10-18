"""Synthetic optical-tracking feed (what a computer-vision system would output).

From the ground-truth event stream we render ball trajectories at 10 Hz:
  frame = {t, ball: {x, y, z}, possessor_id | None}
with Gaussian position noise, dropped frames and possessor flicker, the
typical failure modes of a real vision pipeline. Events that vision can't see
(cards, subs, fouls, restarts, period markers) arrive on a separate
"officials" channel, as they do from a real data provider.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from dataclasses import dataclass

from matchmind.schema import EventType, MatchEvent

HZ = 10
DT = 1.0 / HZ
L = 105.0

OFFICIAL = {
    EventType.KICKOFF, EventType.HALF_TIME, EventType.FULL_TIME, EventType.CARD, EventType.FOUL,
    EventType.SUBSTITUTION, EventType.CORNER, EventType.THROW_IN, EventType.GOAL_KICK,
}


@dataclass
class Frame:
    t: float
    x: float
    y: float
    z: float
    possessor_id: str | None
    possessor_team: str | None


@dataclass
class _Seg:
    t0: float
    t1: float
    x0: float
    y0: float
    x1: float
    y1: float
    z1: float
    possessor: tuple[str, str] | None  # (player_id, team_id) while the ball is controlled


def _segments(ev: MatchEvent, nxt: MatchEvent | None, team_of: dict[str, str]) -> list[_Seg]:
    if ev.x is None:
        return []
    t_next = nxt.t if nxt else ev.t + 1.0
    who = (ev.player_id, ev.team_id) if ev.player_id and ev.team_id else None
    if ev.type == EventType.PASS and ev.end_x is not None:
        dist = math.hypot(ev.end_x - ev.x, ev.end_y - ev.y)
        flight = min(max(dist / max((ev.ball_speed_kmh or 50) / 3.6, 5), 0.3), max(t_next - ev.t - 0.1, 0.3))
        segs = [_Seg(ev.t, ev.t + 0.3, ev.x, ev.y, ev.x, ev.y, 0.0, who)]  # strike
        segs.append(_Seg(ev.t + 0.3, ev.t + 0.3 + flight, ev.x, ev.y, ev.end_x, ev.end_y, 0.0, None))
        receiver = None
        if ev.outcome == "success" and ev.receiver_id:
            receiver = (ev.receiver_id, ev.team_id)
        elif ev.outcome == "fail" and nxt and nxt.player_id and nxt.type in (EventType.INTERCEPTION, EventType.POSSESSION_CHANGE):
            receiver = (nxt.player_id, nxt.team_id)
        if t_next > ev.t + 0.3 + flight:
            segs.append(_Seg(ev.t + 0.3 + flight, t_next, ev.end_x, ev.end_y, ev.end_x, ev.end_y, 0.0, receiver))
        return segs
    if ev.type == EventType.CARRY and ev.end_x is not None:
        return [_Seg(ev.t, t_next, ev.x, ev.y, ev.end_x, ev.end_y, 0.0, who)]
    if ev.type == EventType.SHOT and ev.end_x is not None:
        dist = math.hypot(ev.end_x - ev.x, ev.end_y - ev.y)
        flight = max(dist / max((ev.ball_speed_kmh or 80) / 3.6, 10), 0.3)
        ux, uy = (ev.end_x - ev.x) / max(dist, 1e-6), (ev.end_y - ev.y) / max(dist, 1e-6)
        if ev.outcome == "goal":
            # to the line between the posts, then into the net
            gx = 105.0 if ev.end_x > 52.5 else 0.0
            ly = min(max(ev.end_y, 31.0), 37.0)
            net = gx + (1.0 if gx else -1.0)
            return [_Seg(ev.t, ev.t + 0.2, ev.x, ev.y, ev.x, ev.y, 0.0, who),
                    _Seg(ev.t + 0.2, ev.t + 0.2 + flight, ev.x, ev.y, gx, ly, 1.0, None),
                    _Seg(ev.t + 0.2 + flight, max(t_next, ev.t + 0.4 + flight), net, ly, net, ly, 1.0, None)]
        if ev.outcome == "saved":
            ex, ey, z = ev.end_x - ux * 0.8, ev.end_y - uy * 0.8, 1.0  # caught in front of the line
        elif ev.outcome == "off_target":
            ex, ey, z = ev.end_x + ux * 2.0, ev.end_y + uy * 2.0, 3.2  # over / wide
        else:
            ex, ey, z = ev.end_x, ev.end_y, 0.2  # blocked
        segs = [_Seg(ev.t, ev.t + 0.2, ev.x, ev.y, ev.x, ev.y, 0.0, who),
                _Seg(ev.t + 0.2, ev.t + 0.2 + flight, ev.x, ev.y, ex, ey, z, None)]
        keeper = None
        if ev.outcome == "saved" and nxt and nxt.type == EventType.SAVE:
            keeper = (nxt.player_id, nxt.team_id)
        segs.append(_Seg(ev.t + 0.2 + flight, max(t_next, ev.t + 0.3 + flight), ex, ey, ex, ey, z, keeper))
        return segs
    if ev.type in (EventType.GOAL, EventType.SAVE):
        return []  # the ball is already rendered by the shot
    return [_Seg(ev.t, t_next, ev.x, ev.y, ev.x, ev.y, 0.0, who)]


def tracking_feed(events: list[MatchEvent], seed: int = 0, noise_m: float = 0.15, drop_rate: float = 0.01,
                  flicker_rate: float = 0.004) -> Iterator[tuple[str, MatchEvent | Frame]]:
    """Yields ("official", event) and ("frame", Frame) in time order."""
    rng = random.Random(seed * 7919 + 1)
    team_of = {e.player_id: e.team_id for e in events if e.player_id and e.team_id}
    for i, ev in enumerate(events):
        if ev.type in OFFICIAL:
            yield "official", ev
        nxt = events[i + 1] if i + 1 < len(events) else None
        for seg in _segments(ev, nxt, team_of):
            n = max(1, round((seg.t1 - seg.t0) * HZ))
            for k in range(n):
                if rng.random() < drop_rate:
                    continue
                f = k / n
                static = seg.x0 == seg.x1 and seg.y0 == seg.y1
                # the ball climbs over the first 30% of its flight
                z = 0.0 if seg.possessor is not None else seg.z1 if static else seg.z1 * min(1.0, f / 0.3)
                pos = seg.possessor
                if pos is not None and rng.random() < flicker_rate:
                    pos = None
                yield "frame", Frame(
                    t=round(seg.t0 + k * DT, 2),
                    x=seg.x0 + (seg.x1 - seg.x0) * f + rng.gauss(0, noise_m),
                    y=seg.y0 + (seg.y1 - seg.y0) * f + rng.gauss(0, noise_m),
                    z=z,
                    possessor_id=pos[0] if pos else None,
                    possessor_team=pos[1] if pos else None,
                )
