"""Agent 工具：wiki 三件套、智谱联网搜索、需求更新。"""

from langchain_core.tools import tool

from . import search, wiki


@tool
def wiki_index_tool() -> str:
    """查看社区近一年活动 wiki 的索引（按类型分组的历史活动清单）。"""
    return wiki.wiki_index_text()


@tool
def wiki_search_tool(keyword: str, activity_type: str = "") -> str:
    """按关键词/活动类型检索历史活动，返回命中的页面 slug 与摘要；拿到 slug 后用 wiki_read_tool 读细节。"""
    hits = wiki.wiki_search(keyword, activity_type)
    if not hits:
        return "没有找到相关历史活动。"
    return "\n".join(
        f"- {h['name']}（{h['activity_type']}，{h['time'] or '时间未标注'}）slug={h['slug']}｜亮点：{h['highlights'][:40]}"
        for h in hits
    )


@tool
def wiki_read_tool(slug: str) -> str:
    """读取某个历史活动页面的完整内容（含形式要点、亮点、推文原文）。"""
    return wiki.wiki_read(slug)


@tool
async def web_search_tool(query: str) -> str:
    """联网搜索热词、当下热门玩法或其它高校/社区的活动策划案例，返回标题+摘要+链接。"""
    try:
        results = await search.search_web(query, count=5)
    except search.SearchUnavailable as e:
        return f"搜索不可用：{e}。请如实告知用户本次未联网，并改用你掌握的模型知识。"
    if not results:
        return "没有搜到相关结果。"
    return "\n".join(
        f"- {r['title']}｜{r['media']}｜{r['date']}\n  {r['snippet']}\n  {r['link']}"
        for r in results
    )


@tool
def update_brief_tool(fields_json: str) -> str:
    """把本轮从用户处确认的需求写入需求卡。fields_json 为 JSON 对象字符串，
    可用键：theme(主题)/activity_types(类型数组)/when(时间)/people(人数)/budget(预算)/venue(场地)/extras(特殊要求)。只传需要更新的键。"""
    return f"已记录：{fields_json}"


AGENT_TOOLS = [wiki_index_tool, wiki_search_tool, wiki_read_tool, web_search_tool, update_brief_tool]
