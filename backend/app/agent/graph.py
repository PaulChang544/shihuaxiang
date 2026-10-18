"""LangGraph 状态图与轮次运行器（事件流供 SSE 转发）。"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from .. import db, llm, prompts
from ..schemas import Idea
from . import sessions
from .tools import AGENT_TOOLS

CHECKPOINT_PATH = db.ROOT / "storage" / "langgraph.db"
_checkpointer = None
_checkpointer_cm = None  # 持有异步上下文管理器，避免其被回收时关闭底层 SQLite 连接


async def init_checkpointer() -> None:
    """应用启动时调用一次：打开 SQLite 检查点连接。"""
    global _checkpointer, _checkpointer_cm
    if _checkpointer is None:
        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_PATH))
        _checkpointer = await _checkpointer_cm.__aenter__()


def get_checkpointer():
    return _checkpointer or InMemorySaver()


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    brief: dict
    research: dict
    idea: dict | None
    confirmed: bool
    force: bool
    idea_id: int | None
    session_id: int | None


INTERVIEW_SYSTEM = """你是十号楼学生社区的活动策划助手，通过对话帮用户把想法细化成可落地的活动点子。
工作方式：
1. 每次回复只问 1~2 个最关键的缺口问题（主题/类型、时间、人数、预算、场地、特殊要求），不要一次问一堆；
2. 用户提到历史活动或需要灵感时，先用 wiki 工具查社区近一年做法，再回答（引用具体活动名）；
3. web_search 联网查热词和别校/社区的活动案例：用户明确要时查；讨论主题或玩法、而你对当下流行什么没把握时，也应主动查一次再答（每个话题最多查 1~2 次，不要每轮都搜拖慢回复；工具返回"搜索不可用"时如实说明未联网）；
4. 每确认一项信息，立刻调用 update_brief_tool 记录；
5. 关键信息基本齐了（主题 + 时间 + 人数即可），主动问"信息齐了，要不要我把这次的需求汇总成点子存入点子库？"，不要擅自结束；
6. 语气简洁友好，一次回复不超过 120 字。

社区硬约束：活动室 10 人以内、圆桌不可移动、投影大屏、无音响麦克风；常规预算 200 元内、留发票；
提前一周报备指导老师并报备宿管借场地；部门为社长/副社长、秘书部、宣发部、组织策划部；
党团/思政元素仅在主题团日、党建或重要节点自然融入。"""

SUMMARIZE_PROMPT = """把下面这场对话里已确认的需求，汇总成一个活动点子（JSON 对象，不要包 ideas 数组）。
{idea_spec}

已确认需求：{brief}
最近对话：
{convo}"""


def _writer():
    """节点内取自定义事件写入器；图外/测试直接调用时返回 None。"""
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return None


def _title_model(model):
    """标题生成是与图内调用相互独立的一次轻量调用，这里取模型的独立副本，
    避免与图内调用共用同一响应序列（测试注入的假模型只有一份脚本，被标题调用
    吃掉一条就会打乱工具轮/汇总轮）。真实模型每次调用本就相互独立，副本无副作用。"""
    if model is None:
        return None
    try:
        return model.model_copy(deep=True)
    except Exception:  # 副本构造失败（含不可深拷贝的客户端）时退回原模型
        return model


def _brief_block(brief: dict) -> str:
    return "；".join(f"{k}：{v}" for k, v in (brief or {}).items()) or "（尚未收集到需求信息）"


def _unwrap_idea(raw: str) -> str:
    data = llm.parse_json(raw)
    if isinstance(data, dict) and isinstance(data.get("ideas"), list) and data["ideas"]:
        return json.dumps(data["ideas"][0], ensure_ascii=False)
    return raw


def build_graph(*, model=None, tools=None, checkpointer=None):
    tool_list = AGENT_TOOLS if tools is None else tools
    base_model = llm.build_chat_model(model)
    if tool_list:
        try:
            llm_with_tools = base_model.bind_tools(tool_list)
            interview_stream = True
        except NotImplementedError:
            # 假模型/不支持工具绑定的桩模型：直接用原模型，并改走非流式调用
            # （这类模型在流式路径下不会产出工具调用分块，整条响应会被丢弃）
            llm_with_tools = base_model
            interview_stream = False
    else:
        llm_with_tools = base_model
        interview_stream = True

    async def interview(state: AgentState):
        msgs = state.get("messages") or []
        last = msgs[-1] if msgs else None
        if not isinstance(last, (HumanMessage, ToolMessage)):
            # 运行器以 Command(goto="summarize") 直进汇总（force / confirm 模式）时，
            # LangGraph 仍会走 START→interview 这条边；此时没有待答复的用户输入，
            # 直接跳过，既不消耗模型调用也不产生多余回复。
            return {}
        writer = _writer()
        if writer:
            writer({"type": "step", "key": "interview", "title": "采访与调研", "status": "start"})
        sys = INTERVIEW_SYSTEM + f"\n\n已确认需求：{_brief_block(state.get('brief') or {})}"
        prompt = [SystemMessage(content=sys)] + state["messages"]
        kwargs = {} if interview_stream else {"stream": False}
        try:
            reply = await llm_with_tools.ainvoke(prompt, **kwargs)
        except ValueError as e:
            # 少数模型（如只含工具调用、无文本的响应）流式不产出任何分块，langchain 会抛
            # "No generations found in stream."；退化为非流式调用（真实模型正常流式，不走这里）。
            if "No generations found in stream" not in str(e):
                raise
            reply = await llm_with_tools.ainvoke(prompt, stream=False)  # 非流式重试
        if writer:
            writer({"type": "step", "key": "interview", "status": "done"})
        return {"messages": [reply]}

    def apply_brief(state: AgentState):
        brief = dict(state.get("brief") or {})
        for m in reversed(state["messages"]):
            if isinstance(m, ToolMessage) and m.name == "update_brief_tool":
                try:
                    update = json.loads(str(m.content).replace("已记录：", "", 1))
                    if isinstance(update, dict):
                        brief.update({k: v for k, v in update.items() if v})
                except ValueError:
                    pass
                break
        writer = _writer()
        if writer and brief:
            writer({"type": "brief", "fields": brief})
        return {"brief": brief}

    async def summarize(state: AgentState):
        writer = _writer()
        if writer:
            writer({"type": "step", "key": "summarize", "title": "汇总需求", "status": "start"})
        convo = "\n".join(
            f"{'用户' if isinstance(m, HumanMessage) else '助手'}：{m.content}"
            for m in state["messages"][-8:] if isinstance(m.content, str) and m.content
        )
        raw = await llm.chat(
            [("system", SUMMARIZE_PROMPT.format(
                idea_spec=prompts.IDEA_JSON_SPEC,
                brief=_brief_block(state.get("brief") or {}),
                convo=convo))],
            json_mode=True, model=model,
        )
        idea = llm.parse_model(_unwrap_idea(raw), Idea).model_dump()
        if writer:
            writer({"type": "step", "key": "summarize", "status": "done", "detail": idea.get("name", "")})
        return {"idea": idea}

    def confirm(state: AgentState):
        if state.get("force"):
            return {"confirmed": True}
        decision = interrupt({"idea": state.get("idea") or {}})
        if decision is True:
            return {"confirmed": True}
        if isinstance(decision, str) and decision.strip():
            return {"confirmed": False, "messages": [HumanMessage(content=decision)]}
        return {"confirmed": False}

    async def save_idea(state: AgentState):
        """点子确认后落库（替代原版的策划案生成：Agent 产物终点即点子库）。"""
        writer = _writer()
        if writer:
            writer({"type": "step", "key": "save", "title": "存入点子库", "status": "start"})
        idea = state.get("idea") or {}
        data = Idea(**idea).model_dump()
        with db.get_conn() as conn:
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
            idea_id = cur.lastrowid
        if writer:
            writer({"type": "step", "key": "save", "status": "done"})
            writer({"type": "final", "idea_id": idea_id, "title": data["name"]})
        return {"idea_id": idea_id}

    def route_after_interview(state: AgentState):
        last = state["messages"][-1] if state["messages"] else None
        return "tools" if getattr(last, "tool_calls", None) else "end"

    g = StateGraph(AgentState)
    g.add_node("interview", interview)
    g.add_node("summarize", summarize)
    g.add_node("confirm", confirm)
    g.add_node("save_idea", save_idea)
    g.add_edge(START, "interview")
    if tool_list:
        g.add_node("tools", ToolNode(tool_list))
        g.add_node("apply_brief", apply_brief)
        g.add_conditional_edges("interview", route_after_interview, {"tools": "tools", "end": END})
        g.add_edge("tools", "apply_brief")
        g.add_edge("apply_brief", "interview")
    else:
        g.add_conditional_edges("interview", route_after_interview, {"end": END})
    g.add_edge("summarize", "confirm")
    g.add_conditional_edges("confirm", lambda s: "save" if s.get("confirmed") else "interview",
                            {"save": "save_idea", "interview": "interview"})
    g.add_edge("save_idea", END)
    return g.compile(checkpointer=checkpointer or get_checkpointer())


async def stream_turn(session_id: int, message: str | None = None, *, model=None, tools=None,
                      checkpointer=None, mode: str = "chat") -> AsyncIterator[dict]:
    """推进一轮对话并产出事件流。mode: chat | force | confirm。"""
    session = sessions.get_session(session_id)
    if session is None:
        yield {"type": "error", "message": "会话不存在"}
        return

    graph = build_graph(model=model, tools=tools, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(session_id)}}

    if message:
        sessions.append_message(session_id, "user", message)
        if not session["title"] and not session["title_locked"]:
            title = await sessions.generate_title(message, model=_title_model(model))
            if title and sessions.set_title_if_untitled(session_id, title):
                yield {"type": "title", "title": title}

    snapshot = await graph.aget_state(config)
    interrupted = any(t.interrupts for t in (snapshot.tasks or ()))
    if interrupted:
        resume_value = True if mode == "force" else (message or False)
        graph_input = Command(resume=resume_value, update={"session_id": session_id})
    elif mode in ("force", "confirm"):
        graph_input = Command(goto="summarize",
                              update={"session_id": session_id, "force": mode == "force"})
    elif message:
        graph_input = {"messages": [HumanMessage(content=message)], "session_id": session_id}
    else:
        yield {"type": "error", "message": "缺少消息内容"}
        return

    assistant_text: list[str] = []
    call_args: dict = {}  # 流式工具调用的参数分片按 index 累积，前端卡片随之实时补全输入
    tool_calls: list[dict] = []  # 本轮工具调用按序记录（工具名/入参/输出），落库供历史对话回放
    tool_slots: dict = {}  # call_id（缺省时退回工具名）-> tool_calls 下标

    def _record_start(cid: str, name: str, args_text: str) -> None:
        key = cid or f"?{name}"
        if key not in tool_slots:
            tool_slots[key] = len(tool_calls)
            tool_calls.append({"tool": name, "call_id": cid, "input": "", "output": ""})
        tool_calls[tool_slots[key]]["input"] = args_text

    def _persist_tools() -> None:
        for rec in tool_calls:
            sessions.append_message(session_id, "tool", json.dumps(rec, ensure_ascii=False))

    try:
        async for stream_mode, payload in graph.astream(
                graph_input, config, stream_mode=["messages", "custom"]):
            if stream_mode == "custom":
                if payload.get("type") == "brief" and isinstance(payload.get("fields"), dict):
                    # 需求卡随对话持久化，重开会话可还原（apply_brief 每轮带全量）
                    sessions.set_brief(session_id, payload["fields"])
                yield payload
                continue
            chunk, meta = payload
            node = meta.get("langgraph_node")
            if node == "tools" and isinstance(chunk, ToolMessage):
                call_args.clear()  # 一轮模型调用的参数已收集完毕，下一轮重新累积
                cid = getattr(chunk, "tool_call_id", "") or ""
                pos = tool_slots.get(cid or f"?{chunk.name or ''}")
                if pos is not None:
                    tool_calls[pos]["output"] = str(chunk.content)[:2000]
                yield {"type": "tool", "tool": chunk.name or "", "status": "done",
                       "call_id": cid, "detail": str(chunk.content)[:2000]}
                continue
            if node != "interview":
                continue
            text = getattr(chunk, "text", "") or ""
            if isinstance(text, str) and text:
                assistant_text.append(text)
                yield {"type": "token", "text": text}
            chunks = getattr(chunk, "tool_call_chunks", None) or []
            if chunks:
                for c in chunks:
                    key = c.get("index")
                    if key is None:
                        key = c.get("id") or c.get("name") or len(call_args)
                    slot = call_args.setdefault(key, {"id": "", "name": "", "args": ""})
                    slot["id"] = c.get("id") or slot["id"]
                    slot["name"] = c.get("name") or slot["name"]
                    frag = c.get("args")
                    if isinstance(frag, str):
                        slot["args"] += frag
                    elif frag:
                        slot["args"] += json.dumps(frag, ensure_ascii=False)
                    if slot["name"]:
                        yield {"type": "tool", "tool": slot["name"], "status": "start",
                               "call_id": slot["id"], "input": slot["args"][:2000]}
                        _record_start(slot["id"], slot["name"], slot["args"][:2000])
                continue
            for call in (getattr(chunk, "tool_calls", None) or []):
                # 桩模型/非流式路径：完整消息只有 tool_calls，没有 tool_call_chunks
                args = call.get("args", {}) if isinstance(call, dict) else {}
                cid = call.get("id", "") if isinstance(call, dict) else ""
                name = call.get("name", "") if isinstance(call, dict) else ""
                args_text = json.dumps(args, ensure_ascii=False)[:2000]
                yield {"type": "tool", "tool": name, "status": "start",
                       "call_id": cid, "input": args_text}
                _record_start(cid, name, args_text)
    except llm.AIError as e:
        _persist_tools()
        yield {"type": "error", "message": str(e)}
        return
    except Exception as e:  # 兜底，避免 SSE 中断无提示
        _persist_tools()
        yield {"type": "error", "message": f"运行出错：{str(e)[:150]}"}
        return

    _persist_tools()
    if assistant_text:
        sessions.append_message(session_id, "assistant", "".join(assistant_text))
    sessions.touch_session(session_id)

    snapshot = await graph.aget_state(config)
    if any(t.interrupts for t in (snapshot.tasks or ())):
        yield {"type": "confirm", "idea": (snapshot.values.get("idea") or {})}


async def force_generate(session_id: int, *, model=None, tools=None, checkpointer=None) -> AsyncIterator[dict]:
    async for ev in stream_turn(session_id, None, model=model, tools=tools,
                                checkpointer=checkpointer, mode="force"):
        yield ev
