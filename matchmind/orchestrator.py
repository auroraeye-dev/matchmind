"""Multi-agent orchestrator.

    event --> Interpreter --(Moment)--> Explainer --> Narrator --> Personalizer xN --> Renderer --> publish
                  |                                                                       ^
                  +---------------- shared MatchState (blackboard) ------------------------+

* The Interpreter runs inline on every event (deterministic, sub-millisecond).
* Each Moment is handed off to an async task so slow LLM calls never block ingest.
* Every handoff is traced (agent, status, source, latency) and streamed to the UI.
* Failure recovery: per-call timeout, circuit breaker (ResilientLLM), rules
  fallback at every stage, and load shedding when the moment backlog grows.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from matchmind.agents.explainer import Explainer, rules_explain
from matchmind.agents.interpreter import Interpreter
from matchmind.agents.narrator import Narrator, rules_narrate
from matchmind.agents.personalizer import Personalizer, rules_personalize, wants
from matchmind.agents.renderer import Renderer
from matchmind.config import Settings, get_settings
from matchmind.llm.provider import LLMUnavailable, ResilientLLM, build_provider
from matchmind.schema import MatchEvent, MatchMeta, Moment, Persona, TraceRecord

Publish = Callable[[dict[str, Any]], Awaitable[None]]
MAX_BACKLOG = 6  # pending moments before low-importance ones skip the LLM


class Orchestrator:
    def __init__(self, meta: MatchMeta, personas: list[Persona], publish: Publish,
                 settings: Settings | None = None, llm: ResilientLLM | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or ResilientLLM(
            build_provider(self.settings), self.settings.llm_timeout_s, self.settings.llm_max_concurrency
        )
        self.interpreter = Interpreter(meta)
        self.explainer = Explainer(self.llm, self.settings.llm_min_importance)
        self.narrator = Narrator(self.llm)
        self.personalizer = Personalizer(self.llm, self.settings.llm_min_importance)
        self.renderer = Renderer()
        self.personas: dict[str, Persona] = {p.id: p for p in personas}
        self.publish = publish
        self.pending: set[asyncio.Task] = set()
        self.traces: list[TraceRecord] = []

    @property
    def state(self):
        return self.interpreter.state

    def update_persona(self, p: Persona) -> None:
        self.personas[p.id] = p

    # ------------------------------------------------------------------ ingest

    async def on_event(self, ev: MatchEvent) -> None:
        t0 = time.perf_counter()
        derived, moments = self.interpreter.apply(ev)
        await self.publish({"type": "event", "event": ev.model_dump(mode="json"), "derived": derived,
                            "stats": self.state.public_stats()})
        for p in list(self.personas.values()):
            tag = self.renderer.player_tag(ev, derived, p, self.state)
            if tag:
                await self.publish({"type": "overlay", "overlay": tag.model_dump(mode="json")})
        if moments:
            await self._trace(TraceRecord(agent="interpreter", moment_id=moments[0].id, status="ok", source="rules",
                                          ms=(time.perf_counter() - t0) * 1000,
                                          note=", ".join(m.type for m in moments)))
        for m in moments:
            shed = len(self.pending) >= MAX_BACKLOG and m.importance < 0.6
            task = asyncio.create_task(self._handle_moment(m, shed))
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)

    async def drain(self, timeout: float = 30.0) -> None:
        if self.pending:
            await asyncio.wait(set(self.pending), timeout=timeout)

    # ------------------------------------------------------------------ moment pipeline

    async def _handle_moment(self, m: Moment, shed: bool) -> None:
        s = self.state
        try:
            await self.publish({"type": "moment", "moment": m.model_dump(mode="json")})
            x = await self._stage("explainer", m, None, shed,
                                  lambda: self.explainer.run(m, s), lambda: rules_explain(m, s))
            n = await self._stage("narrator", m, None, shed,
                                  lambda: self.narrator.run(m, x, s), lambda: rules_narrate(m, x, s))

            async def per_persona(p: Persona) -> None:
                ok, rel = wants(m, p)
                if not ok:
                    await self._trace(TraceRecord(agent="personalizer", moment_id=m.id, persona_id=p.id,
                                                  status="skipped", source="none", ms=0.0,
                                                  note=f"relevance {rel:.2f} below threshold"))
                    return
                pn = await self._stage("personalizer", m, p.id, shed,
                                       lambda: self.personalizer.run(m, x, n, p, s, rel),
                                       lambda: rules_personalize(m, x, n, p, s, rel))
                t0 = time.perf_counter()
                for ov in self.renderer.render(m, pn, p, s):
                    await self.publish({"type": "overlay", "overlay": ov.model_dump(mode="json")})
                await self._trace(TraceRecord(agent="renderer", moment_id=m.id, persona_id=p.id, status="ok",
                                              source="rules", ms=(time.perf_counter() - t0) * 1000))

            await asyncio.gather(*(per_persona(p) for p in list(self.personas.values())))
        except Exception as exc:
            await self._trace(TraceRecord(agent="orchestrator", moment_id=m.id, status="error", source="none",
                                          ms=0.0, note=f"{type(exc).__name__}: {exc}"))

    async def _stage(self, agent: str, m: Moment, persona_id: str | None, shed: bool,
                     run: Callable[[], Awaitable[Any]], fallback: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        if shed:
            out = fallback()
            await self._trace(TraceRecord(agent=agent, moment_id=m.id, persona_id=persona_id, status="fallback",
                                          source="rules", ms=(time.perf_counter() - t0) * 1000, note="load shed"))
            return out
        try:
            out = await run()
            status, note = "ok", ""
        except LLMUnavailable as exc:
            offline = str(exc) == "offline"
            out, status, note = fallback(), ("ok" if offline else "fallback"), ("offline rules" if offline else str(exc)[:120])
        await self._trace(TraceRecord(agent=agent, moment_id=m.id, persona_id=persona_id, status=status,
                                      source=getattr(out, "source", "rules"),
                                      ms=(time.perf_counter() - t0) * 1000, note=note))
        return out

    async def _trace(self, tr: TraceRecord) -> None:
        self.traces.append(tr)
        await self.publish({"type": "trace", "trace": tr.model_dump(mode="json")})
