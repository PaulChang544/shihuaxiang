"""DeepSeek Web Search 封装（独立工具接口，Bearer = 主 DeepSeek API Key）。

文档：https://api-docs.deepseek.com/zh-cn/guides/search
POST https://api.deepseek.com/v1/tools/search，body {query, count, need_content}；
query 1-128 字符；无流式；按次计费。"""

import httpx

from .. import db

DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
TIMEOUT = 30.0


class SearchUnavailable(Exception):
    pass


async def search_web(query: str, count: int = 5, client: httpx.AsyncClient | None = None) -> list[dict]:
    key = (db.get_setting("api_key") or "").strip()
    if not key:
        raise SearchUnavailable("未配置 DeepSeek API Key（到「设置」页 AI 接入的 API Key 后可用）")
    payload = {"query": query[:128], "count": max(1, min(50, count)), "need_content": False}
    own = client is None
    c = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        resp = await c.post(DEEPSEEK_SEARCH_URL, json=payload,
                            headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        raise SearchUnavailable(f"搜索网络错误：{e}") from e
    finally:
        if own:
            await c.aclose()
    if resp.status_code != 200:
        raise SearchUnavailable(f"搜索失败（{resp.status_code}）：{resp.text[:150]}")
    try:
        results = resp.json().get("results") or []
    except ValueError as e:
        raise SearchUnavailable("搜索响应不是合法 JSON") from e
    return [{"title": r.get("title", ""), "link": r.get("url", ""),
             "snippet": (r.get("snippet") or r.get("summary") or "")[:200],
             "media": r.get("site_name", ""), "date": (r.get("date") or "")[:10]}
            for r in results]
