"""Agent 对话模式接口：会话管理 + SSE 对话流。"""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import auth, llm
from ..agent import graph as agent_graph
from ..agent import sessions

router = APIRouter(prefix="/api/agent")

OPENING = "你好！我是社区活动策划助手。想办一场什么样的活动？可以随便聊——比如目标人群、大概时间、你想要的感觉，我帮你把想法细化成活动点子。"
OPENING_PROMPT = "用一句轻松的开场白（不超过 50 字）向用户打招呼，并邀请他描述想办的活动。只输出开场白。"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class MessageIn(BaseModel):
    message: str = ""


class SessionPatch(BaseModel):
    title: str | None = None
    status: str | None = None


@router.post("/sessions")
async def create_session(request: Request):
    actor = auth.current_member(request)
    s = sessions.create_session(actor["id"])
    try:
        opening = (await llm.chat([("system", OPENING_PROMPT), ("human", "开始")])).strip()[:80]
    except llm.AIError:
        opening = OPENING
    if not opening:
        opening = OPENING
    sessions.append_message(s["id"], "assistant", opening)
    return {"id": s["id"], "opening": opening}


def _owned(sid: int, viewer) -> dict:
    """会话归属校验：本人或管理员可见；他人会话按不存在处理，避免暴露存在性。"""
    s = sessions.get_session(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    owner = s.get("member_id")
    if owner is not None and owner != viewer["id"] and viewer["role"] not in auth.MANAGER_ROLES:
        raise HTTPException(404, "会话不存在")
    return s


@router.get("/sessions")
def list_sessions(request: Request, status: str = "active"):
    viewer = auth.current_member(request)
    if viewer["role"] in auth.MANAGER_ROLES:
        return sessions.list_sessions(status)
    return sessions.list_sessions(status, member_id=viewer["id"])


@router.get("/sessions/{sid}")
def get_session(sid: int, request: Request):
    s = _owned(sid, auth.current_member(request))
    return {"session": s, "messages": sessions.get_messages(sid), "brief": sessions.get_brief(sid)}


@router.patch("/sessions/{sid}")
def patch_session(sid: int, body: SessionPatch, request: Request):
    _owned(sid, auth.current_member(request))
    return sessions.update_session(sid, title=body.title, status=body.status)


@router.delete("/sessions/{sid}")
def delete_session(sid: int, request: Request):
    _owned(sid, auth.current_member(request))
    sessions.delete_session(sid)
    return {"ok": True}


def _stream(agen):
    async def wrap():
        try:
            async for ev in agen:
                yield _sse(ev.get("type", "message"), ev)
        except Exception as e:  # 兜底，保证前端能收到错误
            yield _sse("error", {"message": f"服务异常：{str(e)[:200]}"})
    return StreamingResponse(wrap(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/sessions/{sid}/stream")
async def stream_chat(sid: int, body: MessageIn, request: Request):
    _owned(sid, auth.current_member(request))
    return _stream(agent_graph.stream_turn(sid, body.message.strip()))


@router.post("/sessions/{sid}/generate")
async def force_generate(sid: int, request: Request):
    _owned(sid, auth.current_member(request))
    return _stream(agent_graph.force_generate(sid))
