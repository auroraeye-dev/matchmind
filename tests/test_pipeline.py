import asyncio
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from matchmind.agents.interpreter import Interpreter, pass_difficulty
from matchmind.config import get_settings
from matchmind.llm.provider import ResilientLLM
from matchmind.orchestrator import Orchestrator
from matchmind.schema import Persona
from matchmind.sim.engine import generate_match, shot_xg

# ---------------------------------------------------------------- simulator


def test_same_seed_same_match():
    a = [e.model_dump() for e in generate_match(11)[1]]
    b = [e.model_dump() for e in generate_match(11)[1]]
    assert a == b


@pytest.mark.parametrize("seed", [1, 7, 42, 99])
def test_match_is_football_realistic(seed):
    meta, events = generate_match(seed)
    c = Counter(e.type.value for e in events)
    passes = [e for e in events if e.type.value == "pass"]
    acc = sum(e.outcome == "success" for e in passes) / len(passes)
    assert 600 < len(passes) < 1300
    assert 0.7 < acc < 0.9
    assert 15 <= c["shot"] <= 50
    assert c["kickoff"] >= 2 and c["half_time"] == 1 and c["full_time"] == 1
    assert c["substitution"] == 6
    for e in events:
        if e.x is not None:
            assert -1 <= e.x <= 106 and -1 <= e.y <= 69
    assert events == sorted(events, key=lambda e: e.t)
    score = events[-1].extra["score"]
    assert score[meta.home.id] == sum(1 for e in events if e.type.value == "goal" and e.team_id == meta.home.id)


def test_xg_model_is_sane():
    penalty, _ = shot_xg(94.0, 34.0, 1, 0.0, False)
    long_range, _ = shot_xg(75.0, 34.0, 1, 0.0, False)
    tight_angle, _ = shot_xg(103.0, 5.0, 1, 0.0, False)
    assert 0.2 < penalty < 0.45
    assert long_range < 0.06
    assert tight_angle < penalty


def test_pass_difficulty_monotonic():
    assert pass_difficulty(40, 0.8, 20) > pass_difficulty(10, 0.1, 0)


# ---------------------------------------------------------------- interpreter


def test_interpreter_stats_and_moments():
    meta, events = generate_match(7)
    it = Interpreter(meta)
    moments = []
    for e in events:
        moments += it.apply(e)[1]
    s = it.state
    assert abs(sum(s.possession_pct().values()) - 100) < 0.5
    assert s.score == events[-1].extra["score"]
    types = Counter(m.type for m in moments)
    assert types["goal"] == sum(s.score.values())
    assert types["full_time"] == 1 and types["half_time"] == 1
    assert -1 <= s.momentum <= 1 and 0 <= s.chaos <= 1
    recap = next(m for m in moments if m.type == "full_time")
    assert len(recap.evidence["goals"]) == sum(s.score.values())


# ---------------------------------------------------------------- orchestrator


async def _run(personas, llm, seed=7):
    meta, events = generate_match(seed)
    out = []

    async def pub(m):
        out.append(m)

    orch = Orchestrator(meta, personas(meta), pub, settings=get_settings(), llm=llm)
    for e in events:
        await orch.on_event(e)
        await asyncio.sleep(0)
    await orch.drain()
    return meta, out


def _personas(meta):
    return [
        Persona(id="analyst", mode="analyst", metric="xg"),
        Persona(id="fan", mode="casual", favorite_team_id=meta.home.id, language="es"),
    ]


async def test_personas_get_different_experiences():
    _meta, out = await _run(_personas, ResilientLLM(None))
    ovs = [m["overlay"] for m in out if m["type"] == "overlay" and m["overlay"]["kind"] != "player_tag"]
    by = Counter(o["persona_id"] for o in ovs)
    assert by["analyst"] > by["fan"] > 0, "casual fan should see fewer, bigger moments"
    fan_goals = [o for o in ovs if o["persona_id"] == "fan" and o["data"]["moment_type"] == "goal"]
    assert fan_goals and all(o["title"] == "¡GOL!" for o in fan_goals)
    assert any(o["kind"] == "recap" for o in ovs)
    assert not [m for m in out if m["type"] == "trace" and m["trace"]["status"] == "error"]


class FakeLLM:
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    async def complete_json(self, system, user, max_tokens=350, agent="agent"):
        self.calls += 1
        if self.fail:
            raise TimeoutError("model down")
        if "Explainer" in system:
            return {"headline": "LLM headline", "why": "LLM why.", "evidence": ["a 1", "b 2"], "confidence": 0.8}
        if "Narrator" in system:
            return {"text": "LLM commentary."}
        return {"headline": "LLM persona", "body": "LLM persona body."}


async def test_llm_path_used_for_important_moments():
    fake = FakeLLM()
    _, out = await _run(_personas, ResilientLLM(fake, timeout_s=2))
    traces = [m["trace"] for m in out if m["type"] == "trace"]
    assert fake.calls > 0
    assert any(t["source"] == "llm" and t["agent"] == "explainer" for t in traces)
    ovs = [m["overlay"] for m in out if m["type"] == "overlay"]
    assert any(o["source"] == "llm" for o in ovs)


async def test_failing_llm_falls_back_and_opens_breaker():
    fake = FakeLLM(fail=True)
    llm = ResilientLLM(fake, timeout_s=1, max_failures=3, cooldown_s=999)
    _, out = await _run(_personas, llm)
    traces = [m["trace"] for m in out if m["type"] == "trace"]
    assert fake.calls == 3, "breaker should stop calling a dead model after 3 failures"
    assert any(t["status"] == "fallback" for t in traces)
    assert not [t for t in traces if t["status"] == "error"]
    assert any(m["type"] == "overlay" and m["overlay"]["kind"] == "recap" for m in out)


# ---------------------------------------------------------------- server


def test_server_rest_and_websocket(monkeypatch):
    monkeypatch.setattr("matchmind.server.MAX_GAP_S", 0.0)
    from matchmind.server import app

    client = TestClient(app)
    assert client.get("/api/health").json()["ok"]
    ds = client.get("/api/dataset?seed=3").json()
    assert ds["meta"]["synthetic"] is True and len(ds["events"]) > 1000
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"seed": 3, "speed": 600, "personas": [{"id": "a", "mode": "analyst"}]})
        kinds = Counter()
        while True:
            msg = ws.receive_json()
            kinds[msg["type"]] += 1
            if msg["type"] == "end":
                break
    assert kinds["meta"] == 1 and kinds["event"] > 1000 and kinds["overlay"] > 10


# ---------------------------------------------------------------- auto-eventing


def test_vision_auto_eventing_accuracy():
    from matchmind.agents.vision import auto_event_stream, evaluate
    from matchmind.sim.tracking import tracking_feed

    for seed in (3, 11):
        meta, truth = generate_match(seed)
        detected = list(auto_event_stream(meta, tracking_feed(truth, seed)))
        r = evaluate(truth, detected)
        assert r["pass"]["recall"] > 0.95 and r["pass"]["precision"] > 0.95
        assert r["score"]["detected"] == {k: v for k, v in r["score"]["truth"].items() if v}
        assert [e.t for e in detected] == sorted(e.t for e in detected)


async def test_tracking_feed_runs_through_agents():
    from matchmind.agents.vision import auto_event_stream
    from matchmind.sim.tracking import tracking_feed

    meta, truth = generate_match(5)
    out = []

    async def pub(m):
        out.append(m)

    orch = Orchestrator(meta, _personas(meta), pub, settings=get_settings(), llm=ResilientLLM(None))
    for e in auto_event_stream(meta, tracking_feed(truth, 5)):
        await orch.on_event(e)
        await asyncio.sleep(0)
    await orch.drain()
    goals = [m for m in out if m["type"] == "moment" and m["moment"]["type"] == "goal"]
    assert len(goals) == sum(truth[-1].extra["score"].values())
    assert any(m["type"] == "overlay" and m["overlay"]["kind"] == "recap" for m in out)


# ---------------------------------------------------------------- guardrail


def test_grounding_guardrail():
    from matchmind.llm.guardrails import UngroundedOutput, check_grounded, enforce

    payload = {"xg": 0.34, "possession_pct": 61.2, "clock": "67:12", "shots": 9}
    assert check_grounded({"why": "a 0.34 xG chance, 61% possession, 34% chance, in the 67th minute, 9 shots"}, payload) == []
    assert check_grounded({"why": "his 4th goal from 27 metres"}, payload) == [4.0, 27.0]
    with pytest.raises(UngroundedOutput):
        enforce({"why": "a 0.81 xG chance"}, payload)


async def test_hallucinating_llm_is_blocked_and_falls_back():
    class Liar(FakeLLM):
        async def complete_json(self, system, user, max_tokens=350, agent="agent"):
            self.calls += 1
            return {"headline": "Wonder goal", "why": "From 47 metres at 187 km/h.", "evidence": ["xG 0.93"], "text": "77 goals", "body": "999"}

    _, out = await _run(_personas, ResilientLLM(Liar(), timeout_s=2))
    traces = [m["trace"] for m in out if m["type"] == "trace"]
    assert any(t["status"] == "fallback" and "guardrail" in t["note"] for t in traces)
    ovs = [m["overlay"] for m in out if m["type"] == "overlay" and m["overlay"]["kind"] != "player_tag"]
    assert ovs and not any("187" in o["body"] or "999" in o["body"] for o in ovs)


def test_session_recording_and_overlay_export(monkeypatch):
    monkeypatch.setattr("matchmind.server.MAX_GAP_S", 0.0)
    from matchmind.server import app

    client = TestClient(app)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"seed": 4, "speed": 600, "personas": [{"id": "a", "mode": "analyst"}, {"id": "f", "mode": "casual", "language": "es"}]})
        sid = None
        while True:
            msg = ws.receive_json()
            sid = sid or msg.get("session_id")
            if msg["type"] == "end":
                break
    assert sid and any(s["id"] == sid and s["finished"] for s in client.get("/api/sessions").json())
    feed = client.get(f"/api/sessions/{sid}/overlays.json?persona=f&tags=false").json()
    assert feed["format"] == "matchmind.overlay-feed/1" and feed["count"] > 0
    assert all(o["persona_id"] in ("f", "*") and o["t_out"] > o["t_in"] for o in feed["overlays"])
    assert [o["t_in"] for o in feed["overlays"]] == sorted(o["t_in"] for o in feed["overlays"])
    assert "properties" in client.get("/api/schema/overlay").json()
    assert client.get("/api/sessions/nope/overlays.json").status_code == 404


def test_trained_vision_model_beats_rules():
    from matchmind.agents.vision import FEATURES, auto_event_stream, evaluate, load_model
    from matchmind.sim.tracking import tracking_feed

    model = load_model()
    assert model and model["features"] == FEATURES and model["validation"]["f1"] > 0.9
    rules_p, model_p = [], []
    for seed in (8, 9):
        meta, truth = generate_match(seed)
        rules_p.append(evaluate(truth, list(auto_event_stream(meta, tracking_feed(truth, seed), model=None)))["shot"]["precision"])
        model_p.append(evaluate(truth, list(auto_event_stream(meta, tracking_feed(truth, seed), model=True)))["shot"]["precision"])
    assert sum(model_p) > sum(rules_p)


def test_vision_training_pipeline_runs(tmp_path):
    from matchmind.agents.vision_train import train

    m = train([301, 302], [303], out=tmp_path / "m.json")
    assert (tmp_path / "m.json").exists() and len(m["weights"]) == 13 and 0 < m["threshold"] < 1


async def test_fan_lines_are_localised_offline():
    _meta, out = await _run(lambda m: [Persona(id=f"fan_{lang}", mode="casual", favorite_team_id=m.home.id, language=lang)
                                      for lang in ("es", "fr", "hi")], ResilientLLM(None))
    ovs = [m["overlay"] for m in out if m["type"] == "overlay" and m["overlay"]["kind"] != "player_tag"]
    for lang, marker in (("es", "¡"), ("fr", " !"), ("hi", "।")):
        mine = [o for o in ovs if o["persona_id"] == f"fan_{lang}"]
        assert mine and all(o["language"] == lang for o in mine)
        assert any(marker in o["body"] for o in mine)


async def test_persona_change_repersonalises_recent_moments():
    meta, events = generate_match(7)
    out = []

    async def pub(m):
        out.append(m)

    orch = Orchestrator(meta, [Persona(id="v", mode="analyst")], pub, llm=ResilientLLM(None))
    for e in events[:1200]:
        await orch.on_event(e)
        await asyncio.sleep(0)
    await orch.drain()
    fan = Persona(id="v", mode="casual", favorite_team_id=meta.away.id, language="es")
    orch.update_persona(fan)
    redone = await orch.reapply(fan)
    assert 0 < len(redone) <= 8
    assert all(o["persona_id"] == "v" and o["data"]["restyled"] and o["data"]["mode"] == "casual" for o in redone)
    assert all(o["language"] == "es" for o in redone)
    assert [o["t_match"] for o in redone] == sorted(o["t_match"] for o in redone)


def test_guardrail_ignores_confidence_and_window():
    from matchmind.llm.guardrails import check_grounded

    payload = {"evidence": {"shots_5min": 2, "possession_last_5min_pct": 62}}
    assert check_grounded({"why": "62% of the ball over the last 5 minutes", "confidence": 0.86}, payload) == []
