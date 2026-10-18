# Copilot instructions for MatchMind

MatchMind is a multi-agent pipeline that turns SYNTHETIC football events into explainable, personalised broadcast overlays.

## Architecture
- `matchmind/sim/` is the seeded match engine (`engine.py`) and a synthetic 10 Hz tracking feed (`tracking.py`). Never add real clubs, players or footage.
- `matchmind/agents/` has one agent per pipeline stage:
  - Vision (auto-eventing)
  - Interpreter (deterministic stats and moments)
  - Explainer
  - Narrator
  - Personalizer
  - Renderer
- `matchmind/orchestrator.py` runs the handoffs, traces every stage and handles failure: timeouts, circuit breaker, rules fallback and load shedding.
- `matchmind/llm/provider.py` runs every model call as a Microsoft Agent Framework `Agent`. Backends are Foundry, Azure OpenAI, OpenAI-compatible, GitHub Models, or offline.
- `matchmind/mcp_server.py` exposes match intelligence as MCP tools.
- `matchmind/integrations/` holds the Cosmos DB, Fabric Eventstream and Azure AI Translator integrations.

## Rules
- LLM-backed agents must always have a deterministic rules path, and must only cite numbers that exist in `MatchState`.
- Keep the Interpreter LLM-free and fast, because it runs on every event.
- New pipeline outputs need a pydantic model in `schema.py` and a test in `tests/`.
- Run `pytest -q` and `ruff check .` before committing.

## Useful MCP servers (see `.vscode/mcp.json`)
- `matchmind`: query match state, moments, explanations and player tags while developing.
- `azure` (Azure MCP Server): inspect the deployed Container App, Cosmos DB and Foundry resources.
