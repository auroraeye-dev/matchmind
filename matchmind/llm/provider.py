"""LLM providers behind one tiny interface, plus a circuit breaker.

Supported (pick via LLM_PROVIDER, or "auto"):
  azure   - Azure OpenAI / Microsoft Foundry deployment (AZURE_OPENAI_*)
  openai  - any OpenAI-compatible endpoint, e.g. a Foundry project's /openai/v1 (OPENAI_*)
  github  - GitHub Models, free for prototyping with a GitHub token (GITHUB_TOKEN)
  offline - no LLM; every agent uses its deterministic rules path
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol

from matchmind.config import Settings


class LLMUnavailable(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    async def complete_json(self, system: str, user: str, max_tokens: int = 350) -> dict[str, Any]: ...


class _ChatProvider:
    """Shared chat-completions call for any client exposing the OpenAI SDK surface."""

    def __init__(self, name: str, client: Any, model: str):
        self.name = name
        self.client = client
        self.model = model

    async def complete_json(self, system: str, user: str, max_tokens: int = 350) -> dict[str, Any]:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=0.6,
        )
        return json.loads(resp.choices[0].message.content or "{}")


def build_provider(s: Settings) -> LLMProvider | None:
    choice = s.llm_provider
    if choice == "auto":
        if s.azure_endpoint and s.azure_api_key:
            choice = "azure"
        elif s.openai_base_url and s.openai_api_key:
            choice = "openai"
        elif s.github_token:
            choice = "github"
        else:
            choice = "offline"

    if choice == "azure":
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            azure_endpoint=s.azure_endpoint, api_key=s.azure_api_key, api_version=s.azure_api_version
        )
        return _ChatProvider(f"azure:{s.azure_deployment}", client, s.azure_deployment)
    if choice == "openai":
        from openai import AsyncOpenAI

        return _ChatProvider(f"openai:{s.openai_model}", AsyncOpenAI(base_url=s.openai_base_url, api_key=s.openai_api_key), s.openai_model)
    if choice == "github":
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url="https://models.github.ai/inference", api_key=s.github_token)
        return _ChatProvider(f"github:{s.github_model}", client, s.github_model)
    return None


class ResilientLLM:
    """Timeout + bounded concurrency + circuit breaker around a provider.

    After `max_failures` consecutive failures the breaker opens for `cooldown_s`
    and every call fails fast, so agents drop straight to their rules path and
    the live feed never stalls waiting on a model.
    """

    def __init__(self, provider: LLMProvider | None, timeout_s: float = 6.0, max_concurrency: int = 4,
                 max_failures: int = 3, cooldown_s: float = 60.0):
        self.provider = provider
        self.timeout_s = timeout_s
        self.sem = asyncio.Semaphore(max_concurrency)
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.open_until = 0.0
        self.calls = 0
        self.errors = 0

    @property
    def name(self) -> str:
        return self.provider.name if self.provider else "offline"

    @property
    def available(self) -> bool:
        return self.provider is not None and time.monotonic() >= self.open_until

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": self.available,
            "breaker_open": self.provider is not None and time.monotonic() < self.open_until,
            "calls": self.calls,
            "errors": self.errors,
        }

    async def complete_json(self, system: str, user: str, max_tokens: int = 350) -> dict[str, Any]:
        if not self.available:
            raise LLMUnavailable("offline" if self.provider is None else "circuit open")
        async with self.sem:
            self.calls += 1
            try:
                out = await asyncio.wait_for(self.provider.complete_json(system, user, max_tokens), self.timeout_s)
            except Exception as exc:
                self.errors += 1
                self.failures += 1
                if self.failures >= self.max_failures:
                    self.open_until = time.monotonic() + self.cooldown_s
                    self.failures = 0
                raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
            self.failures = 0
            return out
