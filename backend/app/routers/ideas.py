import json

from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from .. import db, llm, materials, prompts
from ..agent import search
from ..schemas import HotwordList, IdeaList

router = APIRouter(prefix="/api/ideas")

RETRY_HINT = "你刚才的输出不是合法 JSON，请重新只输出一个 JSON 对象。"


class IdeaRequest(BaseModel):
    activity_types: list[str] = []
    budget: str = ""
    people: str = ""
    month: str = ""
    venue: str = ""


def _save_ideas(items) -> list[dict]:
    out = []
    with db.get_conn() as conn:
        for it in items:
            data = it.model_dump()
            cur = conn.execute(
                "INSERT INTO ideas(name, highlight, hotword, hotword_source, activity_type,"
                " feasibility, star_rating, raw_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    data["name"],
                    data["highlight"],
                    data["hotword"],
                    data["hotword_source"],
                    data["activity_type"],
                    data["feasibility"],
                    data["star_rating"],
                    json.dumps(data, ensure_ascii=False),
                ),
            )
            out.append({**data, "id": cur.lastrowid, "favorited": False})
    return out


async def _search_context(query: str) -> tuple[str, str | None]:
    """真调 DeepSeek Search（复用主 API Key），返回 (注入 prompt 的检索上下文, 错误或 None)。"""
    try:
        results = await search.search_web(query, count=5)
    except search.SearchUnavailable as e:
        return "", str(e)
    if not results:
        return "", "没有搜到相关结果"
    lines = "\n".join(
        f"- {r['title']}｜{r['media']}｜{r['date']}\n  {r['snippet']}\n  {r['link']}" for r in results
    )
    return f"以下是联网检索到的最新相关资料，热词与点子应从中取材或印证：\n{lines}", None


async def _chat_ideas(activity_types, conditions, search_context: str = ""):
    with db.get_conn() as conn:
        mats = materials.summarize_for_types(conn, activity_types)
        past = materials.list_past_names(conn)
    messages = prompts.build_idea_messages(activity_types, conditions, mats, past_names=past)
    if search_context:
        messages = messages + [HumanMessage(content=search_context)]
    raw = await llm.chat(messages, json_mode=True)
    try:
        return llm.parse_model(raw, IdeaList).ideas
    except llm.AIError:
        messages = messages + [AIMessage(content=raw), HumanMessage(content=RETRY_HINT)]
        return llm.parse_model(await llm.chat(messages, json_mode=True), IdeaList).ideas


@router.post("/generate")
async def generate_ideas(req: IdeaRequest):
    types = [t.strip() for t in req.activity_types if t.strip()]
    if not types:
        raise HTTPException(400, "请至少选择一个活动类型")
    conditions = req.model_dump(exclude={"activity_types"})
    web_on = db.get_setting("web_search") == "1"
    ctx, err = (await _search_context(f"{'、'.join(types)} 活动 热门玩法 创意")) if web_on else ("", None)
    items = await _chat_ideas(types, conditions, ctx)
    return {"degraded": bool(err), "ideas": _save_ideas(items)}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/generate/stream")
async def generate_ideas_stream(req: IdeaRequest):
    types = [t.strip() for t in req.activity_types if t.strip()]
    if not types:
        raise HTTPException(400, "请至少选择一个活动类型")
    conditions = req.model_dump(exclude={"activity_types"})

    async def gen():
        model_name = db.get_setting("model") or "默认"
        web_on = db.get_setting("web_search") == "1"
        yield _sse("step", {"key": "config", "title": "读取配置", "brief": "检查服务商配置与生成条件", "status": "start"})
        with db.get_conn() as conn:
            mats = materials.summarize_for_types(conn, types)
        n_mats = len([l for l in mats.split("\n") if l.strip()])
        yield _sse("step", {"key": "config", "status": "done",
                            "detail": f"模型 {model_name} · 联网{'开启' if web_on else '关闭'}"})
        yield _sse("step", {"key": "materials", "title": "检索往年素材", "brief": "从素材库找同类活动作参考", "status": "start"})
        yield _sse("step", {"key": "materials", "status": "done",
                            "detail": f"命中 {n_mats} 条相关素材" if n_mats else "未命中素材，按社区通用情况定制"})

        degraded = False

        async def run_stream(sink: list, msg_list: list, *, event_key: str = "generate", retry_note: str | None = None):
            async for ev in llm.chat_stream(msg_list, json_mode=True):
                if ev["type"] == "text":
                    sink.append(ev["text"])
                    yield _sse("chunk", {"key": "retry" if retry_note else event_key, "text": ev["text"]})

        yield _sse("step", {"key": "search", "title": "联网检索热词",
                            "brief": "用 DeepSeek Search 检索抖音/微博/小红书当下热门活动与话题",
                            "status": "start" if web_on else "done",
                            "detail": None if web_on else "未开启联网（可在设置页打开联网搜索增强）"})
        hotwords_text = ""
        if web_on:
            ctx, err = await _search_context(f"{'、'.join(types)} 活动 热门玩法 热词")
            if err:
                degraded = True
                yield _sse("step", {"key": "search", "status": "done",
                                    "detail": f"联网检索未成功（{str(err)[:60]}），本次使用模型内置知识"})
            else:
                hw_messages = prompts.build_hotword_messages(types) + [HumanMessage(content=ctx)]
                sink: list[str] = []
                try:
                    async for s in run_stream(sink, hw_messages, event_key="search"):
                        yield s
                    hw = llm.parse_model("".join(sink), HotwordList).hotwords
                    names = [w.word for w in hw]
                    hotwords_text = "\n".join(
                        f"- {w.word}（{w.platform}）：{w.desc}｜匹配：{w.fit}" for w in hw
                    )
                    yield _sse("step", {"key": "search", "status": "done",
                                        "detail": f"联网命中资料后提炼出 {len(hw)} 个热词：" + "、".join(n for n in names[:8])})
                except llm.AIError as e:
                    degraded = True
                    yield _sse("step", {"key": "search", "status": "done",
                                        "detail": f"热词提炼未成功（{str(e)[:60]}），后续仅按素材生成"})

        yield _sse("step", {"key": "generate", "title": "AI 生成点子",
                            "brief": "结合已检索热词与往年活动，产出 8 个点子（JSON）", "status": "start"})
        with db.get_conn() as conn:
            past = materials.list_past_names(conn)
        messages = prompts.build_idea_messages(types, conditions, mats, hotwords_text, past_names=past)

        async def _forward(agen):
            async for s in agen:
                yield s

        sink: list[str] = []
        try:
            async for s in _forward(run_stream(sink, messages)):
                yield s
            text = "".join(sink)
        except llm.AIError as e:
            yield _sse("error", {"message": str(e)})
            yield _sse("step", {"key": "generate", "status": "error", "detail": str(e)})
            return

        yield _sse("step", {"key": "parse", "title": "解析校验", "brief": "校验 AI 输出为合法 JSON 点子列表", "status": "start"})
        items = None
        for attempt in (1, 2):
            try:
                items = llm.parse_model(text, IdeaList).ideas
                break
            except llm.AIError as e:
                if attempt == 2:
                    yield _sse("step", {"key": "parse", "status": "error", "detail": str(e)})
                    yield _sse("error", {"message": str(e)})
                    return
                yield _sse("step", {"key": "retry", "title": "自动重试",
                                    "brief": "第 1 次输出不是合法 JSON，已要求重新输出", "status": "start"})
                messages = messages + [AIMessage(content=text), HumanMessage(content=RETRY_HINT)]
                sink = []
                async for s in _forward(run_stream(sink, messages, retry_note="重新生成 JSON 输出")):
                    yield s
                text = "".join(sink)
        yield _sse("step", {"key": "parse", "status": "done", "detail": f"解析出 {len(items)} 个点子"})

        yield _sse("step", {"key": "save", "title": "落库保存", "brief": "点子写入本地数据库", "status": "start"})
        saved = _save_ideas(items)
        yield _sse("step", {"key": "save", "status": "done", "detail": f"已保存 {len(saved)} 条"})
        yield _sse("final", {"degraded": degraded, "ideas": saved})

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
def list_ideas(favorited: str | None = None):
    sql = "SELECT * FROM ideas"
    args: tuple = ()
    if favorited == "1":
        sql += " WHERE favorited=1"
    sql += " ORDER BY id DESC"
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


@router.post("/{idea_id}/favorite")
def toggle_favorite(idea_id: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT favorited FROM ideas WHERE id=?", (idea_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "点子不存在")
        new_val = 0 if row["favorited"] else 1
        conn.execute("UPDATE ideas SET favorited=? WHERE id=?", (new_val, idea_id))
    return {"favorited": bool(new_val)}


@router.delete("/{idea_id}")
def delete_idea(idea_id: int):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM ideas WHERE id=?", (idea_id,))
    return {"ok": True}
