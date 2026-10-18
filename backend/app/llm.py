"""LangChain 版 AI 层：模型构建、调用与流式事件（替代原手写 httpx 客户端）。

- 模型：langchain_openai.ChatOpenAI，按设置切换 Responses / Chat Completions 协议
- 提示词：ChatPromptTemplate（见 prompts.py）
- 调用：LCEL（prompt | model | parser），结构化校验见 schemas.py
- 流式：astream 逐块产出 {"type": "text"|"done"} 事件，供 SSE 轨迹面板消费
"""

import json
from collections.abc import AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from . import db

TIMEOUT = 120.0
MODES = ("responses", "chat")


class AIError(Exception):
    pass


class AIFormatError(AIError):
    """AI 输出不符合预期的结构化格式（可触发重试）。"""


def _settings() -> tuple[str, str, str, str]:
    d = db.SETTING_DEFAULTS
    base = (db.get_setting("api_base") or d["api_base"]).strip()
    key = (db.get_setting("api_key") or "").strip()
    name = (db.get_setting("model") or d["model"]).strip()
    mode = (db.get_setting("api_mode") or d["api_mode"]).strip()
    if mode not in MODES:
        mode = d["api_mode"]
    if not (base and key and name):
        raise AIError("AI 未配置完整，请先到「设置」页填写 API 地址、Key 和模型名")
    return base, key, name, mode


def build_chat_model(model: BaseChatModel | None = None) -> BaseChatModel:
    """按设置构建 ChatOpenAI；测试可注入任意 BaseChatModel 跳过网络。"""
    if model is not None:
        return model
    base, key, name, mode = _settings()
    return ChatOpenAI(
        model=name,
        api_key=key,
        base_url=base,
        use_responses_api=(mode == "responses"),
        timeout=TIMEOUT,
        max_retries=0,
    )


def _bind(model: BaseChatModel, *, json_mode: bool) -> BaseChatModel:
    # 注意：bind_tools 会覆盖此前 bind 的参数，必须先绑工具再绑 response_format
    # 联网检索不走模型端绑定：DeepSeek Search 是独立工具接口（agent/search.py），结果由调用方注入 prompt
    bound = model
    if json_mode:
        bound = bound.bind(response_format={"type": "json_object"})
    return bound


def _as_messages(messages) -> list[BaseMessage]:
    from langchain_core.messages import convert_to_messages

    if messages and isinstance(messages[0], BaseMessage):
        return messages
    return convert_to_messages(messages)


def _error_message(e: Exception) -> str:
    text = str(e)
    if "Connection error" in text or "ConnectError" in text or "timed out" in text.lower():
        return f"网络错误：{text[:200]}"
    return f"AI 调用失败：{text[:300]}"


async def chat(
    messages,
    *,
    json_mode: bool = False,
    model: BaseChatModel | None = None,
) -> str:
    bound = _bind(build_chat_model(model), json_mode=json_mode)
    chain = bound | StrOutputParser()
    try:
        return await chain.ainvoke(_as_messages(messages))
    except AIError:
        raise
    except Exception as e:  # openai SDK / httpx 异常统一转 AIError
        raise AIError(_error_message(e)) from e


def _chunk_events(chunk) -> list[dict]:
    events: list[dict] = []
    text = ""
    try:
        raw_text = getattr(chunk, "text", "")
        if isinstance(raw_text, str):  # langchain 1.x：text 为属性
            text = raw_text
        elif callable(raw_text):  # 兼容旧版方法形式
            text = raw_text()
    except Exception:
        text = ""
    if text:
        events.append({"type": "text", "text": text})

    return events


async def chat_stream(
    messages,
    *,
    json_mode: bool = False,
    model: BaseChatModel | None = None,
) -> AsyncIterator[dict]:
    """流式调用，yield {"type":"text","text":...} 事件，
    最后 yield {"type":"done","text":全文}；失败抛 AIError。"""
    bound = _bind(build_chat_model(model), json_mode=json_mode)
    full: list[str] = []
    try:
        async for chunk in bound.astream(_as_messages(messages)):
            for ev in _chunk_events(chunk):
                if ev["type"] == "text":
                    full.append(ev["text"])
                yield ev
    except AIError:
        raise
    except Exception as e:
        raise AIError(_error_message(e)) from e
    yield {"type": "done", "text": "".join(full)}


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIFormatError(f"AI 输出不是合法 JSON：{text[:200]}") from e


def parse_model(text: str, schema):
    """解析 JSON 并用 pydantic 模型校验；不符合预期时抛 AIFormatError 以触发重试。"""
    try:
        return schema.model_validate(parse_json(text))
    except ValidationError as e:
        raise AIFormatError(f"AI 输出不符合预期结构：{str(e)[:200]}") from e
