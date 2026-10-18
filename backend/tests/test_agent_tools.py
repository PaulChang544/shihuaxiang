import asyncio
import json
import pytest
from backend.app import db
from backend.app.agent import tools, wiki


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO materials(name, activity_type, highlights, raw_text) VALUES('小满节气手作','节气时令','绘心美好','正文')")


def test_tool_bundle_exposed():
    names = {t.name for t in tools.AGENT_TOOLS}
    assert {"wiki_index_tool", "wiki_search_tool", "wiki_read_tool", "web_search_tool", "update_brief_tool"} <= names


def test_wiki_search_tool_returns_readable_text():
    out = asyncio.run(tools.wiki_search_tool.ainvoke({"keyword": "小满"}))
    assert "小满节气手作" in out and "节气时令" in out


def test_web_search_tool_degrades_without_key():
    out = asyncio.run(tools.web_search_tool.ainvoke({"query": "中秋"}))
    assert "搜索不可用" in out and "Key" in out


def test_update_brief_tool_echoes_json():
    payload = json.dumps({"when": "10月下旬"}, ensure_ascii=False)
    out = asyncio.run(tools.update_brief_tool.ainvoke({"fields_json": payload}))
    assert "10月下旬" in out
