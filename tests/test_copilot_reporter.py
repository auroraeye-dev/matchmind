import sys
import types

from matchmind import copilot_reporter


async def test_report_agent_wires_copilot_sdk_to_mcp(monkeypatch, tmp_path):
    captured = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send_and_wait(self, prompt, timeout=60.0):
            captured["prompt"] = prompt
            return types.SimpleNamespace(data=types.SimpleNamespace(content="# Report\nBody"))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def create_session(self, **kw):
            captured.update(kw)
            return FakeSession()

    fake = types.ModuleType("copilot")
    fake.CopilotClient = FakeClient
    fake_session = types.ModuleType("copilot.session")
    fake_session.PermissionHandler = types.SimpleNamespace(approve_all=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "copilot", fake)
    monkeypatch.setitem(sys.modules, "copilot.session", fake_session)

    md = await copilot_reporter.write_report(7)
    assert md.startswith("# Report")
    assert captured["mcp_servers"]["matchmind"]["args"] == ["-m", "matchmind.mcp_server"]
    assert all(t.startswith("mcp:matchmind-") for t in captured["available_tools"])
    assert "seed: 7" in captured["prompt"]
    path = copilot_reporter.save_report(7, md, out_dir=str(tmp_path))
    assert path.read_text().startswith("# Report")
