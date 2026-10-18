"""OpenTelemetry tracing: each agent stage is a span, exported to Azure Application Insights.

Enabled when APPLICATIONINSIGHTS_CONNECTION_STRING is set (azd sets it in Azure).
Agent Framework's own GenAI instrumentation (model, tokens, latency) is turned on too,
so a moment's spans show Interpreter -> Explainer -> model call -> Personalizer -> Renderer.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace

log = logging.getLogger("matchmind.telemetry")
tracer = trace.get_tracer("matchmind")
_enabled = False


def setup_telemetry() -> bool:
    global _enabled
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if _enabled or not conn:
        return _enabled
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn, resource_attributes={"service.name": "matchmind"})
        logging.getLogger("azure").setLevel(logging.WARNING)  # the exporter logs every HTTP call at INFO
        from agent_framework.observability import enable_instrumentation

        enable_instrumentation()
        _enabled = True
    except Exception as exc:  # telemetry must never stop the app
        log.warning("telemetry disabled: %s", exc)
    return _enabled


def enabled() -> bool:
    return _enabled
