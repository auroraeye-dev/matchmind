"""Exercise the real Microsoft Agent Framework path against a local mock OpenAI endpoint."""

import json
import socket
import threading
import time
from dataclasses import replace

import pytest
import uvicorn
from fastapi import FastAPI, Request

from matchmind.config import get_settings
from matchmind.llm.provider import AgentFrameworkProvider, ResilientLLM, build_provider, parse_json

seen: list[dict] = []
mock = FastAPI()


@mock.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    seen.append(body)
    system = body["messages"][0]["content"]
    content = {"headline": "Mock headline", "why": "Mock why.", "evidence": ["x 1"]} if "Explainer" in system else {"text": "Mock"}
    return {
        "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()), "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.fixture(scope="module")
def mock_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    th.join(5)


async def test_agent_framework_provider_round_trip(mock_url):
    s = replace(get_settings(), llm_provider="openai", openai_base_url=mock_url, openai_api_key="test", openai_model="mock-model")
    provider = build_provider(s)
    assert isinstance(provider, AgentFrameworkProvider)
    llm = ResilientLLM(provider, timeout_s=10)
    out = await llm.complete_json("You are the Explainer agent.", "{}", agent="explainer")
    assert out["headline"] == "Mock headline"
    req = seen[-1]
    assert req["model"] == "mock-model"
    assert req["messages"][0]["role"] in ("system", "developer")
    assert req.get("response_format") == {"type": "json_object"}
    # one Agent Framework agent per role
    await llm.complete_json("You are the Narrator agent.", "{}", agent="narrator")
    assert {k[0] for k in provider._agents} == {"explainer", "narrator"}


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("no json here")
