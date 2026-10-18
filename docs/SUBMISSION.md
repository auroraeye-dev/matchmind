# MatchMind: hackathon submission kit

## Checklist against the Official Rules

| Requirement | Where | Status |
|---|---|---|
| New project, created after Sep 29, 2026 | First commit Oct 18, 2026 | ✅ |
| Public GitHub repo | https://github.com/auroraeye-dev/matchmind | ✅ |
| Fits the theme: ingest → interpret → explain → render → personalize | See the [pipeline](../README.md#pipeline) | ✅ |
| Uses synthetic, football-realistic data that we have rights to | Our own seeded engine (`matchmind/sim`). No real clubs, players or footage. | ✅ |
| Short pitch (what, which Microsoft tech, problem solved) | [below](#pitch-paste-into-innovation-studio) | ✅ |
| Demo video under 2:00, public on YouTube or Vimeo, no copyrighted music | [script below](#demo-video-script-150) | ⬜ record and upload |
| Free testing access until judging ends (Nov 10) | `azd up` deploys to Azure Container Apps | ⬜ deploy, then paste the URL |
| English materials | All docs and UI default to English | ✅ |

## Pitch (paste into Innovation Studio)

**MatchMind: explainable match intelligence, personalised for every fan.**

MatchMind is a multi-agent system that watches a live football match and tells each viewer what matters and *why*: a studio analyst, a casual fan, or someone who wants Spanish or Hindi. The input is a synthetic event feed, or a synthetic 10 Hz tracking feed that our Vision agent auto-events. Six cooperating agents turn it into timed, machine-readable broadcast overlays.

**The agents:**
- **Vision** auto-events from ball tracking with a trained shot classifier. On 20 held-out matches it found every goal (100% precision and recall), 100% of passes, and shots with 99% precision.
- **Interpreter** computes xG, pass difficulty, momentum, and a control-vs-chaos index.
- **Explainer** gives grounded reasons that only cite real numbers.
- **Narrator** writes commentary and recaps.
- **Personalizer** decides relevance, tone and language for each viewer.
- **Renderer** produces the lower thirds, stat cards, player tags and recaps.

**Built on Microsoft:**
- **Microsoft Foundry** (gpt-4o-mini). The agents run on the **Microsoft Agent Framework** with keyless managed identity.
- A **MCP server** exposes match intelligence to **GitHub Copilot** and other agents. The **Azure MCP Server** is configured for operations.
- A **GitHub Copilot SDK** agent writes post-match reports through our MCP tools, and the **GitHub CLI** publishes them.
- The app runs on **Azure Container Apps**, with **Azure Cosmos DB** for match memory, **Azure AI Translator** for languages, and a **Microsoft Fabric** Eventstream feeding an Eventhouse for analytics.
- Everything deploys with **azd** and Bicep, and is built with **GitHub Copilot agent mode** and GitHub Actions CI/CD.

**Viewer features:** players get automatic speed and distance tags at set thresholds. There's a player cam, one-click replays, a highlights reel, a full-screen broadcast view, and an exportable timed overlay feed for graphics engines.

**The problem it solves:** broadcasters produce one feed for millions of different viewers. MatchMind produces a different, explainable feed for each of them in real time. It stays reliable even when models fail: every agent has a deterministic fallback, a circuit breaker, a grounding guardrail that blocks invented numbers, and OpenTelemetry tracing to Application Insights.

## Category

Primary: **Best Multi-Agent System**. The project has:
- distinct agent roles
- a shared state blackboard
- explicit handoffs
- failure recovery (timeouts, circuit breaker, rules fallback, load shedding)
- live agent tracing in the UI

It is also eligible for the Grand Prize.

## Hero technologies used

| Technology | How MatchMind uses it | Code |
|---|---|---|
| Microsoft Foundry | Model deployment and project. The agents call it with Entra ID. | `infra/main.bicep`, `llm/provider.py` |
| Microsoft Agent Framework | Each LLM role (Explainer, Narrator, Personalizer) is an Agent Framework `Agent` | `llm/provider.py` |
| MCP (Azure MCP + our server) | `matchmind` MCP server over stdio and HTTP at `/mcp`, plus the Azure MCP Server for operations | `mcp_server.py`, `.vscode/mcp.json` |
| GitHub Copilot agent mode | Development with repo instructions and MCP servers | `.github/copilot-instructions.md` |
| GitHub Copilot SDK | Match Report agent that researches through MCP tools | `copilot_reporter.py` |
| GitHub CLI | Repo setup, publishing reports as gists, `azd pipeline config` | `copilot_reporter.py` |
| Microsoft Fabric | Eventstream sink into an Eventhouse, with KQL analytics | `integrations/sinks.py`, `fabric/matchmind.kql` |
| Azure Apps and AI services | Container Apps, Container Registry, AI Translator, Log Analytics, Application Insights (OpenTelemetry) | `infra/main.bicep`, `integrations/translator.py`, `telemetry.py` |
| Azure databases | Cosmos DB (serverless, Entra-only) storing matches, moments and overlays | `integrations/sinks.py` |

## Demo video script (≈1:50)

| Time | Show | Say |
|---|---|---|
| 0:00–0:12 | Match centre hero, press Kick off | "This is MatchMind. Six AI agents watch a synthetic football match and explain it differently to every viewer." |
| 0:12–0:30 | Live pitch with player tags, momentum chart filling | "The Interpreter tracks xG, pass difficulty, momentum, and whether the game is controlled or chaotic, every event, in real time." |
| 0:30–0:52 | Analyst and fan tickers side by side on a goal | "Same goal, two viewers. The analyst gets the xG and the tactical reason. The home fan gets 'Get in!'. Switch the fan to Spanish and the Personalizer answers in Spanish." |
| 0:52–1:08 | Click a goal marker on the momentum timeline; the replay theatre plays it with lower thirds. Then switch Feed to Tracking. | "Any moment can be replayed in one click. No event data? Our Vision agent runs a trained model over a 10 Hz tracking feed. It found every goal in 20 held-out matches." |
| 1:08–1:25 | Agent trace panel; a fallback status in amber | "Every handoff is traced. If Foundry slows down, a circuit breaker drops to rules, so the broadcast never stalls." |
| 1:25–1:42 | VS Code with Copilot calling `matchmind` MCP tools; a Cosmos DB item in the portal | "Copilot and other agents can query the match through our MCP server. Everything lands in Cosmos DB and Fabric for analytics." |
| 1:42–1:50 | Broadcast view (full screen) | "Foundry, Agent Framework, MCP, Copilot, Container Apps, Cosmos DB, Fabric. MatchMind." |

Use no copyrighted music; the YouTube Audio Library is fine. Upload as Public or Unlisted.

## Testing instructions for judges

1. Open the deployed URL and press **Kick off**. No login is needed.
2. Change a ticker's **Mode**, **Club**, **Language** or **Follow** player while the match runs.
3. Click any marker on the momentum timeline to replay it. Use **▶ Highlights** for the reel and **📺 Broadcast view** for the TV mode.
4. Pick a player in **Line-ups** to open the Player cam. Use **⬇ Overlay feed** to download the timed overlay JSON.
5. Set **Feed** to *Tracking feed* and restart to watch auto-eventing.
6. To use the MCP endpoint, point any MCP client at `<url>/mcp/`.
7. To run locally: `pip install -e ".[dev]" && uvicorn matchmind.server:app`. It works fully offline.
