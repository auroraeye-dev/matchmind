"""Fan-out of pipeline output to Azure data services, off the hot path.

  CosmosSink     - Azure Cosmos DB: one document per match, moment and overlay
                   (partition key /match_id) so recaps and highlights can be queried later.
  EventstreamSink - Microsoft Fabric Eventstream (Event Hubs-compatible custom endpoint):
                   raw events + moments land in a Fabric Lakehouse / KQL database for analytics.

Every sink is optional and enabled by env vars. A bounded queue decouples sinks
from the live feed: if a sink is slow or down, messages are dropped and counted,
the broadcast never waits.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any, Protocol

log = logging.getLogger("matchmind.sinks")


class Sink(Protocol):
    name: str

    async def write(self, batch: list[dict[str, Any]]) -> None: ...

    async def close(self) -> None: ...


class CosmosSink:
    name = "cosmos"
    KINDS = frozenset({"meta", "moment", "overlay"})

    def __init__(self, endpoint: str, database: str = "matchmind", container: str = "match_items",
                 key: str | None = None, client: Any = None):
        self.endpoint, self.database, self.container_name, self.key = endpoint, database, container, key
        self._client = client
        self._container: Any = None

    async def _ensure(self) -> Any:
        if self._container is None:
            if self._client is None:
                from azure.cosmos.aio import CosmosClient

                credential: Any = self.key
                if not credential:
                    from azure.identity.aio import DefaultAzureCredential

                    credential = DefaultAzureCredential()
                self._client = CosmosClient(self.endpoint, credential)
            from azure.cosmos import PartitionKey

            db = await self._client.create_database_if_not_exists(self.database)
            self._container = await db.create_container_if_not_exists(self.container_name, partition_key=PartitionKey("/match_id"))
        return self._container

    async def write(self, batch: list[dict[str, Any]]) -> None:
        container = await self._ensure()
        for msg in batch:
            doc = to_document(msg)
            if doc:
                await container.upsert_item(doc)

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()


def to_document(msg: dict[str, Any]) -> dict[str, Any] | None:
    kind, match_id = msg.get("type"), msg.get("match_id")
    if kind == "meta":
        m = msg["meta"]
        return {"id": m["match_id"], "match_id": m["match_id"], "kind": "match", **m}
    if kind == "moment":
        m = msg["moment"]
        return {"id": f"{match_id}:{m['id']}", "match_id": match_id, "kind": "moment", **m}
    if kind == "overlay" and msg["overlay"]["kind"] != "player_tag":
        o = msg["overlay"]
        return {"id": f"{match_id}:{o['id']}", "match_id": match_id, "kind": "overlay", **o}
    return None


class EventstreamSink:
    name = "fabric_eventstream"
    KINDS = frozenset({"event", "moment"})

    def __init__(self, connection_string: str, eventhub_name: str | None = None, producer: Any = None):
        self.connection_string, self.eventhub_name = connection_string, eventhub_name
        self._producer = producer

    def _ensure(self) -> Any:
        if self._producer is None:
            from azure.eventhub.aio import EventHubProducerClient

            self._producer = EventHubProducerClient.from_connection_string(self.connection_string, eventhub_name=self.eventhub_name)
        return self._producer

    async def write(self, batch: list[dict[str, Any]]) -> None:
        from azure.eventhub import EventData

        producer = self._ensure()
        data = await producer.create_batch()
        for msg in batch:
            row = to_row(msg)
            if row is None:
                continue
            ev = EventData(json.dumps(row, default=str))
            try:
                data.add(ev)
            except ValueError:  # batch full
                await producer.send_batch(data)
                data = await producer.create_batch()
                data.add(ev)
        if len(data):
            await producer.send_batch(data)

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.close()


def to_row(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Flat rows that map cleanly onto a Fabric Lakehouse / Eventhouse table."""
    if msg.get("type") == "event":
        e = msg["event"]
        return {"record": "event", "match_id": e["match_id"], "seq": e["seq"], "t": e["t"], "clock": e["clock"],
                "period": e["period"], "type": e["type"], "team_id": e.get("team_id"), "player_id": e.get("player_id"),
                "x": e.get("x"), "y": e.get("y"), "end_x": e.get("end_x"), "end_y": e.get("end_y"), "outcome": e.get("outcome"),
                "xg": e.get("xg"), "pressure": e.get("pressure"), "ball_speed_kmh": e.get("ball_speed_kmh"),
                "player_speed_kmh": e.get("player_speed_kmh"), **{f"d_{k}": v for k, v in msg.get("derived", {}).items()},
                "momentum": msg.get("stats", {}).get("momentum"), "chaos": msg.get("stats", {}).get("chaos")}
    if msg.get("type") == "moment":
        m = msg["moment"]
        return {"record": "moment", "match_id": msg.get("match_id"), "moment_id": m["id"], "type": m["type"], "t": m["t"],
                "clock": m["clock"], "importance": m["importance"], "team_id": m.get("team_id"), "player_id": m.get("player_id"),
                "evidence": json.dumps(m.get("evidence", {}), default=str)}
    return None


class SinkHub:
    """Bounded, batched, non-blocking fan-out to every configured sink."""

    def __init__(self, sinks: list[Any], max_queue: int = 5000, batch_size: int = 100):
        self.sinks = sinks
        self.queues: dict[str, asyncio.Queue] = {s.name: asyncio.Queue(max_queue) for s in sinks}
        self.batch_size = batch_size
        self.stats: dict[str, dict[str, int]] = {s.name: {"written": 0, "dropped": 0, "errors": 0} for s in sinks}
        self._tasks: list[asyncio.Task] = []

    @property
    def enabled(self) -> bool:
        return bool(self.sinks)

    def start(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._worker(s)) for s in self.sinks]

    def offer(self, msg: dict[str, Any]) -> None:
        for s in self.sinks:
            if msg.get("type") not in s.KINDS:
                continue
            try:
                self.queues[s.name].put_nowait(msg)
            except asyncio.QueueFull:
                self.stats[s.name]["dropped"] += 1

    async def _worker(self, sink: Any) -> None:
        q = self.queues[sink.name]
        while True:
            batch = [await q.get()]
            while len(batch) < self.batch_size and not q.empty():
                batch.append(q.get_nowait())
            try:
                await sink.write(batch)
                self.stats[sink.name]["written"] += len(batch)
            except Exception as exc:
                self.stats[sink.name]["errors"] += 1
                log.warning("sink %s failed: %s", sink.name, exc)
                await asyncio.sleep(2)
            finally:
                for _ in batch:
                    q.task_done()

    async def flush(self, timeout: float = 10.0) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(*(q.join() for q in self.queues.values())), timeout)

    async def close(self) -> None:
        await self.flush()
        for t in self._tasks:
            t.cancel()
        for s in self.sinks:
            with contextlib.suppress(Exception):
                await s.close()


def sinks_from_env() -> list[Any]:
    sinks: list[Any] = []
    if os.getenv("COSMOS_ENDPOINT"):
        sinks.append(CosmosSink(os.environ["COSMOS_ENDPOINT"], os.getenv("COSMOS_DATABASE", "matchmind"),
                                os.getenv("COSMOS_CONTAINER", "match_items"), os.getenv("COSMOS_KEY") or None))
    if os.getenv("FABRIC_EVENTSTREAM_CONNECTION_STRING"):
        sinks.append(EventstreamSink(os.environ["FABRIC_EVENTSTREAM_CONNECTION_STRING"], os.getenv("FABRIC_EVENTSTREAM_NAME") or None))
    return sinks
