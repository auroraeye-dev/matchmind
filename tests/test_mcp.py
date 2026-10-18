import json

from mcp.client import Client

from matchmind.mcp_server import mcp


def _payload(result):
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


async def test_mcp_tools_end_to_end():
    async with Client(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"get_match", "get_match_state", "list_moments", "explain_moment", "personalize_moment",
                "get_player_tag", "get_events"} <= names

        state = _payload(await client.call_tool("get_match_state", {"seed": 7, "minute": 30}))
        assert state["period"] == 1 and "score" in state

        moments = _payload(await client.call_tool("list_moments", {"seed": 7, "min_importance": 0.9}))
        goal = next(m for m in moments if m["type"] == "goal")
        x = _payload(await client.call_tool("explain_moment", {"seed": 7, "moment_id": goal["id"]}))
        assert "xG" in " ".join(x["evidence"])

        p = _payload(await client.call_tool("personalize_moment", {"seed": 7, "moment_id": goal["id"], "language": "es"}))
        assert p["headline"] == "¡GOL!"

        tag = _payload(await client.call_tool("get_player_tag", {"seed": 7, "player_id": "NBR-9", "minute": 60}))
        assert tag["distance_km"] > 3
