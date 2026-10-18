"""FastAPI app: REST for datasets, WebSocket for the live multi-persona feed.

Run:  uvicorn matchmind.server:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

from matchmind.agents.vision import VisionAutoEventer
from matchmind.config import get_settings
from matchmind.integrations.sinks import SinkHub, sinks_from_env
from matchmind.integrations.translator import translator_from_env
from matchmind.llm.provider import ResilientLLM, build_provider
from matchmind.mcp_server import mcp
from matchmind.orchestrator import Orchestrator
from matchmind.schema import Persona, TraceRecord
from matchmind.sim.engine import MatchSimulator, generate_match
from matchmind.sim.tracking import tracking_feed

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_GAP_S = 1.2  # never wait longer than this between events, whatever the speed

# MCP over streamable HTTP at /mcp. Hosts are restricted via MCP_ALLOWED_HOSTS in production.
_hosts = [h for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h]
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=bool(_hosts), allowed_hosts=_hosts),
)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    sinks.start()
    async with mcp.session_manager.run():
        yield
    await sinks.close()


app = FastAPI(title="MatchMind", version="0.2.0", lifespan=lifespan)
app.mount("/mcp", mcp_app)
settings = get_settings()
llm = ResilientLLM(build_provider(settings), settings.llm_timeout_s, settings.llm_max_concurrency)
sinks = SinkHub(sinks_from_env())
translator = translator_from_env()


@app.get("/api/health")
async def health():
    return {"ok": True, "llm": llm.status(), "translator": bool(translator), "sinks": sinks.stats}


@app.get("/api/match")
async def match_meta(seed: int = Query(7, ge=0, le=99999)):
    return MatchSimulator(seed).meta().model_dump(mode="json")


@app.get("/api/dataset")
async def dataset(seed: int = Query(7, ge=0, le=99999)):
    meta, events = generate_match(seed)
    return {"meta": meta.model_dump(mode="json"), "events": [e.model_dump(mode="json") for e in events]}


@app.websocket("/ws/live")
async def live(ws: WebSocket):
    """Client sends {"seed", "speed", "personas": [...]} then may send
    {"type": "persona", "persona": {...}} / {"type": "speed", "speed": n} / {"type": "pause"|"resume"}."""
    await ws.accept()
    cfg = await ws.receive_json()
    seed = int(cfg.get("seed", 7))
    control = {"speed": float(cfg.get("speed", 30)), "paused": False}
    personas = [Persona(**p) for p in cfg.get("personas", [])] or [Persona()]
    feed = cfg.get("feed", "events")
    sim = MatchSimulator(seed)
    meta = sim.meta()
    vision = VisionAutoEventer(meta) if feed == "tracking" else None
    lock = asyncio.Lock()

    async def publish(msg: dict) -> None:
        if sinks.enabled:
            sinks.offer({**msg, "match_id": meta.match_id})
        async with lock:
            await ws.send_json(msg)

    orch = Orchestrator(meta, personas, publish, settings=settings, llm=llm, translator=translator)
    await publish({"type": "meta", "meta": meta.model_dump(mode="json"), "llm": llm.status(), "feed": feed})

    async def reader() -> None:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "persona":
                orch.update_persona(Persona(**msg["persona"]))
            elif kind == "speed":
                control["speed"] = max(1.0, min(600.0, float(msg["speed"])))
            elif kind in ("pause", "resume"):
                control["paused"] = kind == "pause"

    read_task = asyncio.create_task(reader())
    try:
        prev_t = 0.0
        for ev in _source(sim, vision, seed):
            if vision and ev.extra.get("source") == "vision" and vision.detected % 25 == 0:
                await publish({"type": "trace", "trace": TraceRecord(
                    agent="vision", moment_id=None, status="ok", source="rules", ms=0.0,
                    note=f"{vision.detected} events auto-detected from tracking frames").model_dump(mode="json")})
            gap = (ev.t - prev_t) / control["speed"]
            await asyncio.sleep(min(MAX_GAP_S, max(0.0, gap)))
            while control["paused"]:
                await asyncio.sleep(0.2)
            prev_t = ev.t
            await orch.on_event(ev)
            if read_task.done():
                break
        await orch.drain()
        await publish({"type": "end", "llm": llm.status()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        read_task.cancel()
        with contextlib.suppress(Exception):
            await ws.close()


def _source(sim: MatchSimulator, vision: VisionAutoEventer | None, seed: int):
    """Event feed, or events auto-detected by the Vision agent from a 10 Hz tracking feed."""
    if vision is None:
        yield from sim.events()
        return
    for kind, item in tracking_feed(list(sim.events()), seed):
        yield from (vision.official(item) if kind == "official" else vision.frame(item))


@app.get("/api/autoevent/eval")
async def autoevent_eval(seed: int = Query(7, ge=0, le=99999)):
    from matchmind.agents.vision import auto_event_stream, evaluate

    meta, truth = generate_match(seed)
    return evaluate(truth, list(auto_event_stream(meta, tracking_feed(truth, seed))))


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
