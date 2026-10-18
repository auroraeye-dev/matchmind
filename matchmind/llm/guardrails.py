"""Grounding guardrail: a model may only state numbers that exist in its input.

Every number in an LLM answer is checked against the numbers in the payload the
agent sent (allowing rounding and %-vs-fraction forms). Any invented number
rejects the answer, and the agent falls back to its deterministic rules path.
"""

from __future__ import annotations

import json
import re
from typing import Any

from matchmind.llm.provider import LLMUnavailable

_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
# Numbers that are safe without grounding: small counts, football time conventions and the
# Interpreter's 5-minute analysis window (named in payload keys like shots_5min).
_SAFE = {0.0, 1.0, 2.0, 3.0, 5.0, 45.0, 90.0}
# Answer fields that are metadata about the answer, not claims about the match.
_META_KEYS = {"confidence"}


class UngroundedOutput(LLMUnavailable):
    """Raised when a model answer contains numbers that are not in its input."""


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text):
        try:
            out.append(float(m.replace(",", ".")))
        except ValueError:
            continue
    return out


def _allowed(payload: Any) -> list[float]:
    vals: list[float] = []
    for v in numbers(json.dumps(payload, default=str, ensure_ascii=False)):
        vals += [v, v * 100, v / 100]
    for clock in re.findall(r"(\d+)(?:\+(\d+))?:\d\d", json.dumps(payload, default=str)):
        vals.append(float(clock[0]) + float(clock[1] or 0))
    return vals


def check_grounded(answer: dict[str, Any], payload: Any, tol: float = 0.051) -> list[float]:
    """Returns the ungrounded numbers in `answer` (empty list = grounded)."""
    allowed = _allowed(payload)
    bad = []
    claims = {k: v for k, v in answer.items() if k not in _META_KEYS}
    for v in numbers(json.dumps(claims, default=str, ensure_ascii=False)):
        if v in _SAFE:
            continue
        if not any(abs(v - a) <= max(tol, abs(a) * 0.006) or round(a) == v for a in allowed):
            bad.append(v)
    return bad


def enforce(answer: dict[str, Any], payload: Any) -> dict[str, Any]:
    bad = check_grounded(answer, payload)
    if bad:
        raise UngroundedOutput(f"guardrail: ungrounded numbers {bad[:4]}")
    return answer
