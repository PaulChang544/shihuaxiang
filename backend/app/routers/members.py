import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from pypinyin import lazy_pinyin

from .. import auth, db
from ..codes import code_hash as _code_hash, legacy_sha256 as _legacy_sha256

router = APIRouter(prefix="/api/members")
auth_router = APIRouter(prefix="/api/auth")

PUBLIC_FIELDS = ("id", "name", "contact", "role_note", "role", "active")


def _public(row) -> dict:
    """成员公开字段：超管对外一律显示「超管」（系统账号名即最高权限邀请码，绝不外露）。"""
    out = {k: row[k] for k in PUBLIC_FIELDS}
    if out.get("role") == "super":
        out["name"] = "超管"
        out["role_note"] = "内置超级管理员"
    return out

# 名册排序：级别（站长→管理员→成员），级别内按姓名拼音（在册在前）
ROLE_RANK = {"owner": 0, "admin": 1, "member": 2}


def _pinyin_key(name: str) -> str:
    try:
        return "".join(lazy_pinyin(name or ""))
    except Exception:
        return name or ""


def _bool(x):
    return 1 if x else 0


def _has_owner(conn) -> bool:
    """普通站长（owner）是否存在。隐藏超管不计入，否则首次建成员时无人会被引导为站长。"""
    return conn.execute("SELECT 1 FROM members WHERE role='owner' LIMIT 1").fetchone() is not None


def _log(conn, detail: str, actor_id, action: str) -> None:
    db.log_action(conn, detail, actor_id=actor_id, action=action)


class MemberIn(BaseModel):
    name: str = ""
    contact: str = ""
    role_note: str = ""
    active: bool = True


class CodeIn(BaseModel):
    pass


# ---- 邀请码：HMAC 散列（见 codes.py）+ 6 位十六进制 + claim 限流 ----

def _new_code() -> str:
    return secrets.token_hex(3).upper()  # 6 位，仅 0-9A-F


# claim 爆破防护（单实例内存滑动窗口）：每 IP 5 次/分、20 次/时，超限锁 15 分钟
_CLAIM_LOG: dict[str, list[float]] = {}
_CLAIM_LOCK: dict[str, float] = {}
CLAIM_PER_MIN = 5
CLAIM_PER_HOUR = 20
CLAIM_LOCK_SECONDS = 15 * 60


def _client_ip(request: Request) -> str:
    """反代后的真实客户端 IP：优先 X-Forwarded-For / X-Real-IP（应用仅监听本机，由 nginx 设置）。
    直接用 request.client.host 会让所有用户在反代后共享 127.0.0.1 额度（全站互相锁死）。"""
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"


def _claim_guard(ip: str) -> None:
    now = time.time()
    lock_until = _CLAIM_LOCK.get(ip, 0)
    if lock_until > now:
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    events = [t for t in _CLAIM_LOG.get(ip, []) if now - t < 3600]
    if len(events) >= CLAIM_PER_HOUR or sum(1 for t in events if now - t < 60) >= CLAIM_PER_MIN:
        _CLAIM_LOCK[ip] = now + CLAIM_LOCK_SECONDS
        raise HTTPException(429, "尝试过于频繁，请 15 分钟后再试")
    events.append(now)
    _CLAIM_LOG[ip] = events


def _find_member_by_code(conn, code: str):
    """按 HMAC 散列查邀请码对应的成员行；兼容旧的无盐 sha256 行（命中即升级为 HMAC）。
    返回 (member_row, error)。"""
    code = (code or "").strip().upper()
    if not code:
        return None, ("请输入邀请码", 400)
    row = conn.execute(
        "SELECT m.*, i.id AS invite_id FROM invites i JOIN members m ON m.id=i.member_id WHERE i.code_hash=?",
        (_code_hash(code),),
    ).fetchone()
    if row is None:
        legacy = conn.execute(
            "SELECT m.*, i.id AS invite_id FROM invites i JOIN members m ON m.id=i.member_id WHERE i.code_hash=?",
            (_legacy_sha256(code),),
        ).fetchone()
        if legacy is not None:
            conn.execute("UPDATE invites SET code_hash=? WHERE id=?", (_code_hash(code), legacy["invite_id"]))
            row = legacy
    if row is None:
        return None, ("邀请码无效，请联系站长重新获取", 404)
    if not row["active"]:
        return None, ("该成员已移出名册，请联系站长", 403)
    return row, None


def _login(conn, request: Request, response: Response, code: str):
    _claim_guard(_client_ip(request))
    row, err = _find_member_by_code(conn, code)
    if err:
        raise HTTPException(err[1], err[0])
    token = auth.create_session(conn, row["id"])
    auth.set_session_cookie(response, request, token)
    return {"ok": True, "member": _public(row)}


@auth_router.get("/me")
def auth_me(request: Request):
    """会话恢复：凭 HttpOnly Cookie 换回当前成员（前端启动时调用）。"""
    member = auth.current_member(request)
    return {"member": _public(member)}


@auth_router.post("/logout")
def auth_logout(request: Request, response: Response):
    """退出登录：吊销会话并清 Cookie。"""
    token = request.cookies.get(auth.COOKIE_NAME) or ""
    with db.get_conn() as conn:
        auth.revoke_session(conn, token)
    auth.clear_session_cookie(response)
    return {"ok": True}


class ClaimIn(BaseModel):
    code: str


class WhoamiIn(BaseModel):
    code: str


@router.post("/claim")
def claim(code_in: ClaimIn, request: Request, response: Response):
    """用邀请码激活：校验通过后由服务端签发 HttpOnly 会话 Cookie（邀请码不再存前端）。"""
    with db.get_conn() as conn:
        return _login(conn, request, response, code_in.code)


@router.post("/whoami")
def whoami(data: WhoamiIn, request: Request, response: Response):
    """同 claim：POST 体传码避免进入访问日志，成功即发会话 Cookie。"""
    with db.get_conn() as conn:
        return _login(conn, request, response, data.code)


@router.get("")
def list_members(request: Request):
    """名册（需登录）：内置超管不在此列。
    联系方式仅管理员及以上或本人可见；排序：级别 → 在册在前 → 级别内拼音。"""
    viewer = auth.current_member(request)
    is_manager = viewer["role"] in auth.MANAGER_ROLES
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM members WHERE role != 'super' ORDER BY active DESC, id ASC"
        ).fetchall()]
    for r in rows:
        if not is_manager and r["id"] != viewer["id"]:
            r["contact"] = ""
    rows.sort(key=lambda r: (ROLE_RANK.get(r["role"], 3), 0 if r["active"] else 1,
                             _pinyin_key(r["name"]), r["name"]))
    return rows


@router.post("")
def create_member(data: MemberIn, request: Request):
    actor = auth.require_manager(request)
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "姓名不能为空")
    with db.get_conn() as conn:
        bootstrap = not _has_owner(conn)
        cur = conn.execute(
            "INSERT INTO members(name, contact, role_note, active) VALUES(?,?,?,?)",
            (name, data.contact.strip(), data.role_note.strip(), _bool(data.active)),
        )
        row = conn.execute("SELECT * FROM members WHERE id=?", (cur.lastrowid,)).fetchone()
        if bootstrap:
            # 名册还没有站长（隐藏超管不计）时，引导第一位成员即普通站长
            conn.execute("UPDATE members SET role='owner' WHERE id=?", (row["id"],))
            row = conn.execute("SELECT * FROM members WHERE id=?", (row["id"],)).fetchone()
        _log(conn, f"把「{name}」加入了名册", actor["id"], "member_create")
    return _public(row)


@router.put("/{mid}")
def update_member(mid: int, data: MemberIn, request: Request):
    actor = auth.require_manager(request)
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "姓名不能为空")
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise HTTPException(404, "成员不存在")
        if row["is_system"]:
            raise HTTPException(403, "系统账号不可修改")
        if row["role"] == "owner" and not _bool(data.active):
            raise HTTPException(400, "站长无法停用，请先转让站长")
        conn.execute(
            "UPDATE members SET name=?, contact=?, role_note=?, active=? WHERE id=?",
            (name, data.contact.strip(), data.role_note.strip(), _bool(data.active), mid),
        )
        if not _bool(data.active):
            auth.revoke_member_sessions(conn, mid)  # 停用立即踢下线
        _log(conn, f"更新了成员「{name}」", actor["id"], "member_update")
    return {"ok": True}


@router.delete("/{mid}")
def delete_member(mid: int, request: Request):
    actor = auth.require_manager(request)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise HTTPException(404, "成员不存在")
        if row["is_system"]:
            raise HTTPException(403, "系统账号不可删除")
        if row["role"] == "owner":
            raise HTTPException(403, "站长无法直接移出，请先转让站长")
        conn.execute("DELETE FROM members WHERE id=?", (mid,))
        conn.execute("DELETE FROM invites WHERE member_id=?", (mid,))
        affected = [r["activity_id"] for r in conn.execute(
            "SELECT activity_id FROM activity_leads WHERE member_id=?", (mid,)
        ).fetchall()]
        conn.execute("DELETE FROM activity_leads WHERE member_id=?", (mid,))
        conn.execute("UPDATE activities SET lead_id=NULL WHERE lead_id=?", (mid,))
        # 删负责人后若活动已无任何负责人，退回未派发状态，避免「已确认但无人负责」
        for act_id in affected:
            still = conn.execute(
                "SELECT 1 FROM activity_leads WHERE activity_id=?", (act_id,)
            ).fetchone()
            if still is None:
                conn.execute(
                    "UPDATE activities SET dispatch_status='draft', confirmed_at=NULL WHERE id=?", (act_id,)
                )
        auth.revoke_member_sessions(conn, mid)
        db._ensure_owner(conn)
        db._ensure_super(conn)
        _log(conn, f"把「{row['name']}」移出了名册", actor["id"], "member_delete")
    return {"ok": True}


@router.post("/{mid}/transfer")
def transfer_owner(mid: int, request: Request):
    """站长转让（QQ 群规则）：仅站长或超管可发起；目标限在册成员或管理员；
    全站唯一站长——原站长统一降为管理员，主操作者若是超管则身份不变。"""
    runner = auth.require_manager(request)
    if runner["role"] not in ("owner", "super"):
        raise HTTPException(403, "只有站长可以转让站长")
    with db.get_conn() as conn:
        if runner["id"] == mid:
            raise HTTPException(400, "不能转让给自己")
        target = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if target is None:
            raise HTTPException(404, "成员不存在")
        if target["role"] not in ("member", "admin"):
            raise HTTPException(400, "站长只能转让给在册成员或管理员")
        if not target["active"]:
            raise HTTPException(400, "该成员已停用，无法接任站长")
        conn.execute("UPDATE members SET role='admin' WHERE role='owner'")
        conn.execute("UPDATE members SET role='owner' WHERE id=?", (mid,))
        _log(conn,
             (f"将站长转让给「{target['name']}」，自身成为管理员" if runner["role"] == "owner"
              else f"指定「{target['name']}」为站长"),
             runner["id"], "owner_transfer")
        db._ensure_owner(conn)
    return {"ok": True}


@router.post("/{mid}/appoint")
def appoint_admin(mid: int, request: Request):
    """任命管理员：管理员及以上可把「在册成员」升为管理员。"""
    actor = auth.require_manager(request)
    with db.get_conn() as conn:
        target = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if target is None:
            raise HTTPException(404, "成员不存在")
        if target["role"] != "member":
            raise HTTPException(400, "只有普通成员可以被任命为管理员")
        if not target["active"]:
            raise HTTPException(400, "该成员已停用，无法任命")
        conn.execute("UPDATE members SET role='admin' WHERE id=?", (mid,))
        _log(conn, f"将「{target['name']}」任命为管理员", actor["id"], "admin_appoint")
    return {"ok": True}


@router.post("/{mid}/dismiss")
def dismiss_admin(mid: int, request: Request):
    """卸任管理员：管理员只能卸任自己；站长/超管可卸任任意管理员。"""
    actor = auth.require_manager(request)
    with db.get_conn() as conn:
        target = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if target is None:
            raise HTTPException(404, "成员不存在")
        if target["role"] != "admin":
            raise HTTPException(400, "该成员不是管理员")
        if actor["id"] != target["id"] and actor["role"] == "admin":
            raise HTTPException(403, "只有站长可以卸任其他管理员")
        conn.execute("UPDATE members SET role='member' WHERE id=?", (mid,))
        detail = ("卸任了自己的管理员身份" if actor["id"] == target["id"]
                  else f"卸任了「{target['name']}」的管理员")
        _log(conn, detail, actor["id"], "admin_dismiss")
    return {"ok": True}


@router.get("/{mid}/invites")
def list_invites(mid: int, request: Request):
    """列表只显示创建时间与是否固定码（不再回传任何掩码尾号，杜绝缩小搜索空间）。"""
    auth.require_manager(request)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, code_hash, created_at FROM invites WHERE member_id=? ORDER BY id DESC",
            (mid,),
        ).fetchall()
    super_hash = _code_hash(db.SUPER_CODE)
    return [
        {"id": r["id"], "code": "", "created_at": r["created_at"],
         "fixed": 1 if r["code_hash"] == super_hash else 0}
        for r in rows
    ]


@router.post("/{mid}/invites")
def create_invite(mid: int, request: Request):
    actor = auth.require_manager(request)
    with db.get_conn() as conn:
        target = conn.execute("SELECT * FROM members WHERE id=?", (mid,)).fetchone()
        if target is None:
            raise HTTPException(404, "成员不存在")
        if target["is_system"]:
            raise HTTPException(403, "系统账号的固定邀请码由部署配置管理，不在此生成")
        # 一个成员可拥有多枚邀请码；哈希唯一冲突时换码重试
        for _ in range(5):
            code = _new_code()
            code_hash = _code_hash(code)
            try:
                cur = conn.execute(
                    "INSERT INTO invites(member_id, code, code_hash, code_hint) VALUES(?, '', ?, '')",
                    (mid, code_hash),
                )
                break
            except Exception:
                continue
        else:
            raise HTTPException(500, "邀请码生成失败，请重试")
        row = conn.execute("SELECT id, created_at FROM invites WHERE id=?", (cur.lastrowid,)).fetchone()
        _log(conn, f"为「{target['name']}」生成了新的邀请码", actor["id"], "invite_create")
    # 明文只在生成的响应里出现一次；落库已只有散列
    return {"id": row["id"], "code": code, "created_at": row["created_at"]}


@router.delete("/invites/{iid}")
def delete_invite(iid: int, request: Request):
    actor = auth.require_manager(request)
    with db.get_conn() as conn:
        target = conn.execute("SELECT member_id, code_hash FROM invites WHERE id=?", (iid,)).fetchone()
        if target is None:
            raise HTTPException(404, "邀请码不存在")
        if target["code_hash"] == _code_hash(db.SUPER_CODE):
            raise HTTPException(403, "超级管理员邀请码不可作废")
        conn.execute("DELETE FROM invites WHERE id=?", (iid,))
        _log(conn, "作废了一枚邀请码", actor["id"], "invite_delete")
    return {"ok": True}
