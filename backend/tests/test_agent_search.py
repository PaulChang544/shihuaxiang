import asyncio
import httpx
import pytest
from backend.app import db
from backend.app.agent import search


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_search_requires_key():
    with pytest.raises(search.SearchUnavailable, match="DeepSeek API Key"):
        asyncio.run(search.search_web("秋分 活动"))


def test_search_payload_and_parse():
    db.set_setting("api_key", "sk-test")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"results": [
            {"title": "高校中秋活动案例", "snippet": "某校办了投壶游园会……", "url": "https://a.example.com",
             "site_name": "公众号A", "date": "2026-09-01T00:00:00+00:00"},
        ]})

    async def run():
        async with _client(handler) as c:
            return await search.search_web("中秋 校园活动", count=3, client=c)

    out = asyncio.run(run())
    assert captured["url"] == "https://api.deepseek.com/v1/tools/search"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["query"] == "中秋 校园活动"
    assert captured["body"]["count"] == 3
    assert captured["body"]["need_content"] is False
    assert out[0]["title"] == "高校中秋活动案例"
    assert out[0]["link"] == "https://a.example.com"
    assert "投壶" in out[0]["snippet"]
    assert out[0]["date"] == "2026-09-01"


def test_search_http_error_raises_chinese():
    db.set_setting("api_key", "sk-test")

    def handler(request):
        return httpx.Response(429, text="rate limited")

    async def run():
        async with _client(handler) as c:
            return await search.search_web("x", client=c)

    with pytest.raises(search.SearchUnavailable, match="429"):
        asyncio.run(run())
