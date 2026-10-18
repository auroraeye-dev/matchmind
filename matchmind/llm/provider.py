"""LLM providers behind one tiny interface, plus a circuit breaker.

Every model call runs through a Microsoft Agent Framework ``Agent`` (one per
MatchMind agent role), so the Explainer, Narrator and Personalizer are real
Agent Framework agents with their own instructions, telemetry and middleware.

Backends (pick via LLM_PROVIDER, or "auto"):
  foundry - Microsoft Foundry project endpoint + Entra ID (FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL);
            keyless in Azure via managed identity
  azure   - Azure OpenAI deployment with key (AZURE_OPENAI_*)
  openai  - any OpenAI-compatible endpoint (OPENAI_*)
  github  - GitHub Models, free prototyping with a GitHub token (GITHUB_TOKEN)
  offline - no LLM; every agent uses its deterministic rules path
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Protocol

from matchmind.config import Settings


class LLMUnavailable(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    async def complete_json(self, system: str, user: str, max_tokens: int = 350, agent: str = "agent") -> dict[str, Any]: ...


def parse_json(text: str) -> dict[str, Any]:
    """Models sometimes wrap JSON in prose or code fences; take the first object."""
    text = (text or "").strip()
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"no JSON object in model output: {text[:80]!r}") from None
        out = json.loads(m.group(0))
    if not isinstance(out, dict):
        raise ValueError("model output is not a JSON object")
    return out


class AgentFrameworkProvider:
    """Runs each MatchMind role as a Microsoft Agent Framework Agent over a shared chat client."""

    def __init__(self, name: str, chat_client: Any, json_mode: bool = True, model: str = "", responses_api: bool = False):
        self.name = name
        self.client = chat_client
        self.json_mode = json_mode
        # GPT-5 and o-series models reason first: no custom temperature, and reasoning uses output tokens.
        self.reasoning = is_reasoning_model(model)
        self.responses_api = responses_api
        self._agents: dict[tuple[str, str], Any] = {}

    def agent_for(self, role: str, instructions: str) -> Any:
        from agent_framework import Agent

        key = (role, instructions)
        if key not in self._agents:
            self._agents[key] = Agent(self.client, instructions=instructions, name=f"matchmind-{role}",
                                      description=f"MatchMind {role} agent")
        return self._agents[key]

    async def complete_json(self, system: str, user: str, max_tokens: int = 350, agent: str = "agent") -> dict[str, Any]:
        options: dict[str, Any] = {"max_tokens": max(max_tokens * 6, 2000)} if self.reasoning else {
            "max_tokens": max_tokens, "temperature": 0.6}
        if self.reasoning and self.responses_api:
            # live commentary needs speed, not deep thought: minimal reasoning answers in ~1-2 s
            options["reasoning"] = {"effort": "minimal"}
            options["verbosity"] = "low"
        if self.json_mode:
            options["response_format"] = {"type": "json_object"}
        resp = await self.agent_for(agent, system).run(user, options=options)
        return parse_json(resp.text)


def is_reasoning_model(model: str) -> bool:
    m = model.lower().split("/")[-1]
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def _resolve(s: Settings) -> str:
    if s.llm_provider != "auto":
        return s.llm_provider
    if s.foundry_project_endpoint:
        return "foundry"
    if s.azure_endpoint and s.azure_api_key:
        return "azure"
    if s.openai_base_url and s.openai_api_key:
        return "openai"
    if s.github_token:
        return "github"
    return "offline"


def build_provider(s: Settings) -> LLMProvider | None:
    choice = _resolve(s)
    if choice == "offline":
        return None
    if choice == "foundry":
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import DefaultAzureCredential

        client = FoundryChatClient(project_endpoint=s.foundry_project_endpoint, model=s.foundry_model,
                                   credential=DefaultAzureCredential())
        return AgentFrameworkProvider(f"foundry:{s.foundry_model}", client, json_mode=False, model=s.foundry_model,
                                      responses_api=True)

    from agent_framework.openai import OpenAIChatCompletionClient
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    if choice == "azure":
        raw = AsyncAzureOpenAI(azure_endpoint=s.azure_endpoint, api_key=s.azure_api_key, api_version=s.azure_api_version)
        model, label = s.azure_deployment, f"azure:{s.azure_deployment}"
    elif choice == "openai":
        raw = AsyncOpenAI(base_url=s.openai_base_url, api_key=s.openai_api_key)
        model, label = s.openai_model, f"openai:{s.openai_model}"
    elif choice == "github":
        raw = AsyncOpenAI(base_url="https://models.github.ai/inference", api_key=s.github_token)
        model, label = s.github_model, f"github:{s.github_model}"
    else:
        raise ValueError(f"unknown LLM_PROVIDER {choice!r}")
    return AgentFrameworkProvider(label, OpenAIChatCompletionClient(model=model, async_client=raw), model=model)


class ResilientLLM:
    """Timeout + bounded concurrency + circuit breaker around a provider.

    After `max_failures` consecutive failures the breaker opens for `cooldown_s`
    and every call fails fast, so agents drop straight to their rules path and
    the live feed never stalls waiting on a model.
    """

    def __init__(self, provider: LLMProvider | None, timeout_s: float = 6.0, max_concurrency: int = 4,
                 max_failures: int = 3, cooldown_s: float = 60.0, daily_budget: int | None = None):
        self.provider = provider
        self.timeout_s = timeout_s
        self.sem = asyncio.Semaphore(max_concurrency)
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.open_until = 0.0
        self.calls = 0
        self.errors = 0
        # Cost guard for a public deployment: past this many model calls per UTC day, agents use rules.
        self.daily_budget = daily_budget
        self._day = ""
        self.calls_today = 0

    @property
    def name(self) -> str:
        return self.provider.name if self.provider else "offline"

    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day, self.calls_today = today, 0

    @property
    def over_budget(self) -> bool:
        self._roll_day()
        return self.daily_budget is not None and self.calls_today >= self.daily_budget

    @property
    def available(self) -> bool:
        return self.provider is not None and time.monotonic() >= self.open_until and not self.over_budget

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": self.available,
            "breaker_open": self.provider is not None and time.monotonic() < self.open_until,
            "calls": self.calls,
            "errors": self.errors,
            "calls_today": self.calls_today,
            "daily_budget": self.daily_budget,
        }

    async def complete_json(self, system: str, user: str, max_tokens: int = 350, agent: str = "agent") -> dict[str, Any]:
        if not self.available:
            reason = "offline" if self.provider is None else "daily AI budget reached" if self.over_budget else "circuit open"
            raise LLMUnavailable(reason)
        async with self.sem:
            self.calls += 1
            self.calls_today += 1
            try:
                out = await asyncio.wait_for(self.provider.complete_json(system, user, max_tokens, agent=agent), self.timeout_s)
            except Exception as exc:
                self.errors += 1
                self.failures += 1
                if self.failures >= self.max_failures:
                    self.open_until = time.monotonic() + self.cooldown_s
                    self.failures = 0
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
            self.failures = 0
            return out
