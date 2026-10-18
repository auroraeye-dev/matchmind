"""FastAPI app: REST for datasets, WebSocket for the live multi-persona feed.

Run:  uvicorn matchmind.server:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from matchmind.config import get_settings
from matchmind.llm.provider import ResilientLLM, build_provider
from matchmind.orchestrator import Orchestrator
from matchmind.schema import Persona
from matchmind.sim.engine import MatchSimulator, generate_match

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_GAP_S = 1.2  # never wait longer than this between events, whatever the speed

app = FastAPI(title="MatchMind", version="0.1.0")
settings = get_settings()
llm = ResilientLLM(build_provider(settings), settings.llm_timeout_s, settings.llm_max_concurrency)


@app.get("/api/health")
async def health():
    return {"ok": True, "llm": llm.status()}


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
    sim = MatchSimulator(seed)
    meta = sim.meta()
    lock = asyncio.Lock()

    async def publish(msg: dict) -> None:
        async with lock:
            await ws.send_json(msg)

    orch = Orchestrator(meta, personas, publish, settings=settings, llm=llm)
    await publish({"type": "meta", "meta": meta.model_dump(mode="json"), "llm": llm.status()})

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
        for ev in sim.events():
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


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
