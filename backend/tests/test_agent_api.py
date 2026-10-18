import json
import pytest
from backend.app import db
from backend.app.routers import agent as agent_router


def test_session_crud_endpoints(client):
    r = client.post("/api/agent/sessions")
    assert r.status_code == 200
    sid = r.json()["id"]
    assert isinstance(r.json()["opening"], str) and r.json()["opening"]

    client.patch(f"/api/agent/sessions/{sid}", json={"title": "自定义标题"})
    assert client.get(f"/api/agent/sessions/{sid}").json()["session"]["title"] == "自定义标题"

    client.patch(f"/api/agent/sessions/{sid}", json={"status": "archived"})
    assert all(s["id"] != sid for s in client.get("/api/agent/sessions?status=active").json())
    assert any(s["id"] == sid for s in client.get("/api/agent/sessions?status=archived").json())

    assert client.delete(f"/api/agent/sessions/{sid}").json() == {"ok": True}
    assert client.get(f"/api/agent/sessions/{sid}").status_code == 404


def test_stream_endpoint_emits_events(client, monkeypatch):
    from backend.app.agent import graph as agent_graph

    async def fake_turn(session_id, message=None, **kw):
        yield {"type": "token", "text": "你好"}
        yield {"type": "title", "title": "测试会话"}
        yield {"type": "confirm", "idea": {"name": "x"}}

    monkeypatch.setattr(agent_graph, "stream_turn", fake_turn)
    sid = client.post("/api/agent/sessions").json()["id"]
    with client.stream("POST", f"/api/agent/sessions/{sid}/stream", json={"message": "想办活动"}) as r:
        assert r.status_code == 200
        body = r.read().decode()
    events = []
    for block in body.split("\n\n"):
        ev = data = None
        for line in block.split("\n"):
            if line.startswith("event:"): ev = line[6:].strip()
            elif line.startswith("data:"): data = line[5:].strip()
        if ev and data: events.append((ev, json.loads(data)))
    kinds = [e for e, _ in events]
    assert kinds == ["token", "title", "confirm"]
