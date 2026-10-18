from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import auth, db

router = APIRouter(prefix="/api/settings")

KEYS = ("api_base", "api_key", "model", "api_mode", "web_search")
SECRET_KEYS = ("api_key",)
MODES = ("responses", "chat")


def _ensure_super_admin(request: Request):
    """AI 设置仅超管可读写（身份取自服务端会话，站长也不可见）。"""
    return auth.require_super(request)


def _mask(key: str) -> str:
    """密钥脱敏：只露出头尾少量字符，绝不回传完整 Key。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:5]}****{key[-4:]}"


@router.get("/status")
def settings_status(request: Request):
    """成员可见的最小状态：只告诉前端 AI 是否就绪，不泄露任何配置细节。"""
    auth.current_member(request)
    key = (db.get_setting("api_key") or "").strip()
    base = (db.get_setting("api_base") or "").strip()
    model = (db.get_setting("model") or "").strip()
    return {"has_api_key": bool(key), "ai_ready": bool(key and base and model)}


@router.get("")
def get_settings(request: Request):
    _ensure_super_admin(request)
    out = {}
    for k in KEYS:
        val = db.get_setting(k) or db.SETTING_DEFAULTS.get(k, "")
        out[k] = _mask(val) if k in SECRET_KEYS else val
    out["has_api_key"] = bool(db.get_setting("api_key"))
    out["api_key_source"] = db.key_source("api_key")
    return out


class SettingsIn(BaseModel):
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    api_mode: str = "responses"
    web_search: str = "0"
    clear_api_key: bool = False


@router.put("")
def put_settings(data: SettingsIn, request: Request):
    _ensure_super_admin(request)
    db.set_setting("api_base", data.api_base.strip() or db.SETTING_DEFAULTS["api_base"])
    db.set_setting("model", data.model.strip() or db.SETTING_DEFAULTS["model"])
    db.set_setting("api_mode", data.api_mode if data.api_mode in MODES else "responses")
    db.set_setting("web_search", "1" if data.web_search == "1" else "0")
    # 密钥：显式 clear 才清空；留空/回传掩码 = 保持不变；只有新值才更新
    for key, clear_flag in (("api_key", data.clear_api_key),):
        if clear_flag:
            db.set_setting(key, "")
            continue
        submitted = (getattr(data, key) or "").strip()
        if submitted and submitted != _mask(db.get_setting(key) or ""):
            db.set_setting(key, submitted)
    return {"ok": True}


@router.post("/test")
async def test_connection(request: Request, data: SettingsIn | None = None):
    _ensure_super_admin(request)

    from ..llm import chat, AIError

    try:
        text = await chat([{"role": "user", "content": "回复：ok"}])
        return {"ok": True, "message": text[:50]}
    except AIError as e:
        return {"ok": False, "message": str(e)}
