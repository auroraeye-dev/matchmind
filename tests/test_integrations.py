import asyncio
import json

import httpx

from matchmind.integrations.sinks import CosmosSink, EventstreamSink, SinkHub, to_document, to_row
from matchmind.integrations.translator import AzureTranslator
from matchmind.llm.provider import ResilientLLM
from matchmind.orchestrator import Orchestrator
from matchmind.schema import Persona
from matchmind.sim.engine import generate_match


class FakeContainer:
    def __init__(self):
        self.items = {}

    async def upsert_item(self, doc):
        self.items[doc["id"]] = doc


class FakeDB:
    def __init__(self, c):
        self.c = c

    async def create_container_if_not_exists(self, name, partition_key):
        return self.c


class FakeCosmos:
    def __init__(self):
        self.container = FakeContainer()

    async def create_database_if_not_exists(self, name):
        return FakeDB(self.container)

    async def close(self):
        pass


class FakeBatch(list):
    def add(self, ev):
        self.append(ev)


class FakeProducer:
    def __init__(self):
        self.sent = []

    async def create_batch(self):
        return FakeBatch()

    async def send_batch(self, b):
        self.sent.extend(json.loads(next(e.body)) for e in b)

    async def close(self):
        pass


async def _stream(seed=7):
    meta, events = generate_match(seed)
    out = []

    async def pub(m):
        out.append({**m, "match_id": meta.match_id})

    orch = Orchestrator(meta, [Persona(id="a", mode="analyst")], pub, llm=ResilientLLM(None))
    await pub({"type": "meta", "meta": meta.model_dump(mode="json")})
    for e in events[:600]:
        await orch.on_event(e)
        await asyncio.sleep(0)
    await orch.drain()
    return meta, out


async def test_sinkhub_writes_to_cosmos_and_fabric():
    meta, msgs = await _stream()
    cosmos, producer = FakeCosmos(), FakeProducer()
    hub = SinkHub([CosmosSink("https://x", client=cosmos), EventstreamSink("Endpoint=sb://x/", producer=producer)])
    hub.start()
    for m in msgs:
        hub.offer(m)
    await hub.close()
    items = cosmos.container.items.values()
    assert any(d["kind"] == "match" for d in items)
    assert any(d["kind"] == "moment" for d in items) and all(d["match_id"] == meta.match_id for d in items)
    assert not any(d.get("kind") == "overlay" and d["kind"] == "player_tag" for d in items)
    rows = producer.sent
    assert sum(r["record"] == "event" for r in rows) == 600
    assert any(r["record"] == "moment" for r in rows)
    assert hub.stats["cosmos"]["errors"] == 0 and hub.stats["fabric_eventstream"]["dropped"] == 0


async def test_failing_sink_never_blocks():
    class Broken:
        name, KINDS = "broken", {"event"}

        async def write(self, batch):
            raise RuntimeError("down")

        async def close(self):
            pass

    hub = SinkHub([Broken()], max_queue=10)
    hub.start()
    for i in range(50):
        hub.offer({"type": "event", "event": {"i": i}})
    assert hub.stats["broken"]["dropped"] >= 40
    for t in hub._tasks:
        t.cancel()


def test_document_and_row_shapes():
    assert to_document({"type": "event"}) is None
    assert to_row({"type": "overlay"}) is None


async def test_translator_and_orchestrator_translation():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        body = json.loads(req.content)
        return httpx.Response(200, json=[{"translations": [{"text": f"[{req.url.params['to']}] {b['Text']}", "to": req.url.params["to"]}]} for b in body])

    tr = AzureTranslator("k", "westeurope")
    tr.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await tr.translate(["Hello"], to="fr") == ["[fr] Hello"]
    assert calls[0].headers["Ocp-Apim-Subscription-Region"] == "westeurope"
    await tr.translate(["Hello"], to="fr")
    assert len(calls) == 1, "cached"

    meta, events = generate_match(7)
    out = []

    async def pub(m):
        out.append(m)

    orch = Orchestrator(meta, [Persona(id="f", mode="casual", language="fr")], pub, llm=ResilientLLM(None), translator=tr)
    for e in events:
        await orch.on_event(e)
        await asyncio.sleep(0)
    await orch.drain()
    ovs = [m["overlay"] for m in out if m["type"] == "overlay" and m["overlay"]["kind"] != "player_tag"]
    assert ovs and all(o["body"].startswith("[fr]") and o["language"] == "fr" for o in ovs)
    assert any(m["type"] == "trace" and m["trace"]["agent"] == "translator" for m in out)
