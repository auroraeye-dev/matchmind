"""Runtime settings, read from environment / local .env (never committed)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # "auto" picks azure > github > offline based on which credentials exist.
    llm_provider: str
    foundry_project_endpoint: str | None
    foundry_model: str
    azure_endpoint: str | None
    azure_api_key: str | None
    azure_api_version: str
    azure_deployment: str
    openai_base_url: str | None
    openai_api_key: str | None
    openai_model: str
    github_token: str | None
    github_model: str
    llm_timeout_s: float
    llm_max_concurrency: int
    # Only moments at least this important are sent to the LLM; others use rules.
    llm_min_importance: float


def get_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "auto").lower(),
        foundry_project_endpoint=os.getenv("FOUNDRY_PROJECT_ENDPOINT") or None,
        foundry_model=os.getenv("FOUNDRY_MODEL", "gpt-4o-mini"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT") or None,
        azure_api_key=os.getenv("AZURE_OPENAI_API_KEY") or None,
        azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"),
        openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        github_token=os.getenv("GITHUB_TOKEN") or None,
        github_model=os.getenv("GITHUB_MODEL", "openai/gpt-4o-mini"),
        llm_timeout_s=_f("LLM_TIMEOUT_S", 6.0),
        llm_max_concurrency=int(_f("LLM_MAX_CONCURRENCY", 4)),
        llm_min_importance=_f("LLM_MIN_IMPORTANCE", 0.5),
    )
