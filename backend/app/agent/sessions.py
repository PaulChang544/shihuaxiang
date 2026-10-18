"""会话管理：CRUD、消息存取、标题生成。"""

from .. import db, llm

ALLOWED_STATUS = ("active", "archived")

TITLE_PROMPT = (
    "请根据用户这句话，为这次活动策划对话起一个不超过 16 个字的中文标题，"
    "概括活动主题（如“重阳敬老志愿活动”“秋分手工灯笼”）。只输出标题本身，不要标点包裹。"
)


def create_session(member_id: int | None = None) -> dict:
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO chat_sessions(member_id) VALUES(?)", (member_id,))
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_sessions(status: str = "active", member_id: int | None = None) -> list[dict]:
    """member_id=None 返回全部（管理员视角）；否则只返回本人的会话。"""
    with db.get_conn() as conn:
        if member_id is None:
            rows = conn.execute(
                "SELECT * FROM chat_sessions WHERE status=? ORDER BY updated_at DESC, id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chat_sessions WHERE status=? AND member_id=? ORDER BY updated_at DESC, id DESC",
                (status, member_id),
            ).fetchall()
    return [dict(r) for r in rows]


def get_session(sid: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (sid,)).fetchone()
    return dict(row) if row else None


def update_session(sid: int, *, title: str | None = None, status: str | None = None) -> dict:
    with db.get_conn() as conn:
        if title is not None:
            conn.execute("UPDATE chat_sessions SET title=?, title_locked=1 WHERE id=?", (title.strip()[:32], sid))
        if status in ALLOWED_STATUS:
            conn.execute("UPDATE chat_sessions SET status=? WHERE id=?", (status, sid))
        conn.execute("UPDATE chat_sessions SET updated_at=datetime('now','localtime') WHERE id=?", (sid,))
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise KeyError("会话不存在")
    return dict(row)


def touch_session(sid: int, summary: str | None = None) -> None:
    with db.get_conn() as conn:
        if summary is not None:
            conn.execute("UPDATE chat_sessions SET summary=? WHERE id=?", (summary[:60], sid))
        conn.execute("UPDATE chat_sessions SET updated_at=datetime('now','localtime') WHERE id=?", (sid,))


def set_brief(sid: int, brief: dict) -> None:
    """持久化需求卡（已确认信息），重开对话时可还原。"""
    import json as _json
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE chat_sessions SET brief=?, updated_at=datetime('now','localtime') WHERE id=?",
            (_json.dumps(brief, ensure_ascii=False), sid),
        )


def get_brief(sid: int) -> dict:
    import json as _json
    with db.get_conn() as conn:
        row = conn.execute("SELECT brief FROM chat_sessions WHERE id=?", (sid,)).fetchone()
    if row is None:
        return {}
    try:
        return _json.loads(row["brief"] or "{}")
    except ValueError:
        return {}


def set_title_if_untitled(sid: int, title: str) -> bool:
    """标题为空且未锁定才写入，返回是否写入。"""
    with db.get_conn() as conn:
        row = conn.execute("SELECT title, title_locked FROM chat_sessions WHERE id=?", (sid,)).fetchone()
        if row is None or row["title"] or row["title_locked"]:
            return False
        conn.execute("UPDATE chat_sessions SET title=? WHERE id=?", (title.strip()[:32], sid))
    return True


def delete_session(sid: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (sid,))
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (sid,))


def append_message(sid: int, role: str, content: str) -> None:
    with db.get_conn() as conn:
        conn.execute("INSERT INTO chat_messages(session_id, role, content) VALUES(?,?,?)", (sid, role, content))


def get_messages(sid: int) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM chat_messages WHERE session_id=? ORDER BY id", (sid,)
        ).fetchall()
    return [dict(r) for r in rows]


async def generate_title(text: str, *, model=None) -> str:
    try:
        raw = await llm.chat([("system", TITLE_PROMPT), ("human", text[:200])], model=model)
    except llm.AIError:
        return ""
    title = raw.strip().strip("《》\"'。 ").replace("\n", "")[:16]
    return title
