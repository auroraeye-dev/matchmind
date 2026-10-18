import asyncio

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from matchmind.llm.provider import ResilientLLM
from matchmind.orchestrator import Orchestrator
from matchmind.schema import Persona
from matchmind.sim.engine import generate_match


async def test_agent_stages_emit_otel_spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    meta, events = generate_match(2)

    async def pub(_):
        pass

    orch = Orchestrator(meta, [Persona(id="a", mode="analyst")], pub, llm=ResilientLLM(None))
    for e in events[:800]:
        await orch.on_event(e)
        await asyncio.sleep(0)
    await orch.drain()
    names = {s.name for s in exporter.get_finished_spans()}
    assert {"matchmind.interpreter", "matchmind.explainer", "matchmind.personalizer", "matchmind.renderer"} <= names
    span = next(s for s in exporter.get_finished_spans() if s.name == "matchmind.explainer")
    assert span.attributes["matchmind.match_id"] == meta.match_id and "matchmind.moment_id" in span.attributes
