"""Session recorder: keeps each live session's output so it can be replayed or exported.

The export is a timed overlay feed, the format a broadcast graphics engine
(rendering partner) consumes: every overlay has t_in / t_out in match seconds
plus its persona, priority, anchor and payload.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

MAX_SESSIONS = 20
MAX_ITEMS = 25_000


@dataclass
class Session:
    id: str
    seed: int
    feed: str
    match_id: str
    personas: list[dict[str, Any]]
    started: float = field(default_factory=time.time)
    finished: bool = False
    overlays: list[dict[str, Any]] = field(default_factory=list)
    moments: list[dict[str, Any]] = field(default_factory=list)
    events: int = 0

    def record(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "event":
            self.events += 1
        elif kind == "overlay" and len(self.overlays) < MAX_ITEMS:
            self.overlays.append(msg["overlay"])
        elif kind == "moment" and len(self.moments) < MAX_ITEMS:
            self.moments.append(msg["moment"])
        elif kind == "end":
            self.finished = True

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "seed": self.seed, "feed": self.feed, "match_id": self.match_id, "started": self.started,
                "finished": self.finished, "events": self.events, "moments": len(self.moments), "overlays": len(self.overlays),
                "personas": [p.get("id") for p in self.personas]}

    def overlay_feed(self, persona: str | None = None, include_tags: bool = True) -> dict[str, Any]:
        rows = []
        for o in self.overlays:
            if persona and o["persona_id"] not in (persona, "*"):
                continue
            if not include_tags and o["kind"] == "player_tag":
                continue
            rows.append({
                "id": o["id"], "persona_id": o["persona_id"], "kind": o["kind"], "priority": o["priority"],
                "t_in": o["t_match"], "t_out": round(o["t_match"] + o["display_ms"] / 1000, 2), "clock": o["clock"],
                "title": o["title"], "body": o.get("body", ""), "language": o.get("language", "en"),
                "anchor": o.get("anchor"), "data": o.get("data", {}), "source": o.get("source", "rules"),
            })
        rows.sort(key=lambda r: (r["t_in"], -r["priority"]))
        return {"format": "matchmind.overlay-feed/1", "match_id": self.match_id, "seed": self.seed, "feed": self.feed,
                "time_base": "match_seconds", "persona": persona, "count": len(rows), "overlays": rows}


class Recorder:
    def __init__(self) -> None:
        self.sessions: OrderedDict[str, Session] = OrderedDict()

    def start(self, seed: int, feed: str, match_id: str, personas: list[dict[str, Any]]) -> Session:
        s = Session(id=uuid.uuid4().hex[:12], seed=seed, feed=feed, match_id=match_id, personas=personas)
        self.sessions[s.id] = s
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        return s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def list(self) -> list[dict[str, Any]]:
        return [s.summary() for s in reversed(self.sessions.values())]
