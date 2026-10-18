"""Azure AI Translator: translates rules-path text when no LLM translation is available."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_ENDPOINT = "https://api.cognitive.microsofttranslator.com"


class AzureTranslator:
    name = "azure_translator"

    def __init__(self, key: str, region: str | None = None, endpoint: str = DEFAULT_ENDPOINT, timeout_s: float = 3.0):
        self.key, self.region, self.endpoint = key, region, endpoint.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_s)
        self._cache: dict[tuple[str, str], str] = {}

    async def translate(self, texts: list[str], to: str, source: str = "en") -> list[str]:
        missing = [t for t in texts if (t, to) not in self._cache]
        if missing:
            headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
            if self.region:
                headers["Ocp-Apim-Subscription-Region"] = self.region
            resp = await self.client.post(
                f"{self.endpoint}/translate",
                params={"api-version": "3.0", "from": source, "to": to},
                headers=headers,
                json=[{"Text": t} for t in missing],
            )
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()
            for src, row in zip(missing, data, strict=True):
                self._cache[(src, to)] = row["translations"][0]["text"]
        return [self._cache[(t, to)] for t in texts]


def translator_from_env() -> AzureTranslator | None:
    key = os.getenv("TRANSLATOR_KEY")
    if not key:
        return None
    return AzureTranslator(key, os.getenv("TRANSLATOR_REGION") or None, os.getenv("TRANSLATOR_ENDPOINT", DEFAULT_ENDPOINT))
