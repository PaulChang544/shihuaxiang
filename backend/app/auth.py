"""服务端会话认证：HttpOnly Cookie + 服务器端可吊销会话，取代客户端 op_id。

- 邀请码只在 claim/whoami 时校验一次，随后由服务端签发随机会话令牌
- 令牌原文只存在于浏览器 HttpOnly Cookie；库里只存 sha256 令牌
- 角色与操作者身份一律从会话解析，客户端无法伪造
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import db

COOKIE_NAME = "shx_sid"
SESSION_DAYS = 7          # 会话有效期（滑动续期：每次使用接近到期时自动延长）
RENEW_BEFORE = 60 * 60    # 剩余不足 1 小时时续期一次（约每小时至多写库一次）

MANAGER_ROLES = ("admin", "owner", "super")

# 无需登录即可访问的接口（激活与健康检查）
PUBLIC_PATHS = {
    "/api/health",
    "/api/members/claim",
    "/api/members/whoami",
    "/api/auth/logout",
}


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def create_session(conn, member_id: int) -> str:
    """签发会话，返回令牌原文（写 Cookie 用）。"""
    token = secrets.token_hex(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO sessions(id, member_id, expires_at) VALUES(?,?,?)",
        (_hash(token), member_id, expires),
    )
    return token


def resolve_session(conn, token: str):
    """按 Cookie 令牌换回成员行；过期/吊销/停用返回 None。

    滑动续期：有效会话在剩余不足 RENEW_BEFORE 时自动延长到 7 天后。
    返回 (member_row, renewed)；renewed=True 时调用方需重发 Cookie 同步 max_age。"""
    if not token:
        return None, False
    now = datetime.now()
    row = conn.execute(
        "SELECT s.id AS sid, s.expires_at, s.revoked, m.* FROM sessions s"
        " JOIN members m ON m.id = s.member_id WHERE s.id=?",
        (_hash(token),),
    ).fetchone()
    if row is None or row["revoked"] or not row["active"]:
        return None, False
    if row["expires_at"] < now.strftime("%Y-%m-%d %H:%M:%S"):
        return None, False
    renewed = False
    extend_to = now + timedelta(days=SESSION_DAYS)
    if row["expires_at"] < (extend_to - timedelta(seconds=RENEW_BEFORE)).strftime("%Y-%m-%d %H:%M:%S"):
        conn.execute(
            "UPDATE sessions SET expires_at=? WHERE id=?",
            (extend_to.strftime("%Y-%m-%d %H:%M:%S"), row["sid"]),
        )
        renewed = True
    return row, renewed


def revoke_session(conn, token: str) -> None:
    if token:
        conn.execute("UPDATE sessions SET revoked=1 WHERE id=?", (_hash(token),))


def revoke_member_sessions(conn, member_id: int) -> None:
    conn.execute("UPDATE sessions SET revoked=1 WHERE member_id=?", (member_id,))


def set_session_cookie(response, request: Request, token: str) -> None:
    secure = (request.url.scheme == "https") or (request.headers.get("x-forwarded-proto") == "https")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def current_member(request: Request) -> dict:
    """中间件已注入会话成员；未登录由中间件统一 401，这里只兜底。"""
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "请先激活身份")
    return member


def require_manager(request: Request) -> dict:
    member = current_member(request)
    if member["role"] not in MANAGER_ROLES:
        raise HTTPException(403, "仅管理员及以上可执行该操作")
    return member


def require_super(request: Request) -> dict:
    member = current_member(request)
    if member["role"] != "super":
        raise HTTPException(403, "仅超级管理员可操作 AI 设置")
    return member
