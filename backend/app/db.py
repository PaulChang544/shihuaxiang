import os
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "storage" / "storage.db"
UPLOADS_PATH = ROOT / "storage" / "uploads"

# 密钥类设置可被环境变量覆盖（服务器部署推荐方式）：环境变量 > 数据库
ENV_OVERRIDES = {"api_key": "DEEPSEEK_API_KEY"}

# 固定超管邀请码（可用环境变量 SUPER_CODE 覆盖；散列与规范化见 codes.py）
from .codes import SUPER_CODE  # noqa: E402

SETTING_DEFAULTS = {
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-flash",
    "api_mode": "responses",
    "web_search": "0",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    highlight TEXT NOT NULL DEFAULT '',
    hotword TEXT NOT NULL DEFAULT '',
    hotword_source TEXT NOT NULL DEFAULT '',
    activity_type TEXT NOT NULL DEFAULT '',
    feasibility TEXT NOT NULL DEFAULT '',
    star_rating INTEGER NOT NULL DEFAULT 3,
    raw_json TEXT NOT NULL DEFAULT '{}',
    favorited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_title TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    activity_type TEXT NOT NULL DEFAULT '',
    time TEXT NOT NULL DEFAULT '',
    form_points TEXT NOT NULL DEFAULT '',
    highlights TEXT NOT NULL DEFAULT '',
    participation TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    title_locked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    summary TEXT NOT NULL DEFAULT '',
    brief TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    role_note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'member',
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                 -- sha256(会话令牌)；令牌本身只在 HttpOnly Cookie 里
    member_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    activity_type TEXT NOT NULL DEFAULT '',
    idea_id INTEGER,
    lead_id INTEGER,
    dispatch_status TEXT NOT NULL DEFAULT 'draft',
    dispatch_note TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT,
    plan_note TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'dispatch',
    event_date TEXT NOT NULL DEFAULT '',
    plan_ddl TEXT NOT NULL DEFAULT '',
    recruitment_ddl TEXT NOT NULL DEFAULT '',
    procurement_ddl TEXT NOT NULL DEFAULT '',
    recruitment_url TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '',
    summary_note TEXT NOT NULL DEFAULT '',
    report_text TEXT NOT NULL DEFAULT '',
    report_updated_at TEXT,
    xiumi_url TEXT NOT NULL DEFAULT '',
    article_url TEXT NOT NULL DEFAULT '',
    article_title TEXT NOT NULL DEFAULT '',
    article_text TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    reimbursed INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'photo',
    filename TEXT NOT NULL DEFAULT '',
    stored_path TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    caption TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    ref_id INTEGER,
    label TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    op_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_expenses_activity ON expenses(activity_id);
CREATE INDEX IF NOT EXISTS idx_uploads_activity ON uploads(activity_id);
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    code TEXT NOT NULL DEFAULT '',       -- 兼容旧列：迁移后清空，明文不再落库
    code_hash TEXT NOT NULL DEFAULT '',  -- 凭据散列（sha256 大写邀请码），同 API key 存储方式
    code_hint TEXT NOT NULL DEFAULT '',  -- 明文尾 2 位，仅供名册列表辨识
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS activity_leads (
    activity_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    PRIMARY KEY (activity_id, member_id)
);
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    actor_name TEXT NOT NULL DEFAULT '',
    activity_id INTEGER,
    action TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_log_created ON activity_log(id DESC);
CREATE TABLE IF NOT EXISTS upload_sessions (
    id TEXT PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    category TEXT NOT NULL DEFAULT 'photo',
    caption TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0,
    op_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    # WAL 提升「请求 + 后台作业」并发读写体验（-wal/-shm 由 SQLite 管理，备份已按快照处理）
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn) -> None:
    """已有库的增量迁移（幂等）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(activities)")}
    for col in ("plan_ddl", "recruitment_ddl", "procurement_ddl", "recruitment_url"):
        if col not in cols:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(members)")}
    if "role" not in mcols:
        conn.execute("ALTER TABLE members ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
    if "is_system" not in mcols:
        conn.execute("ALTER TABLE members ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0")
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_sessions)")}
    if "member_id" not in ccols:
        conn.execute("ALTER TABLE chat_sessions ADD COLUMN member_id INTEGER")
    icols = {r["name"] for r in conn.execute("PRAGMA table_info(invites)")}
    for col in ("code_hash", "code_hint"):
        if col not in icols:
            conn.execute(f"ALTER TABLE invites ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(uploads)")}
    if "thumb_path" not in ucols:
        conn.execute("ALTER TABLE uploads ADD COLUMN thumb_path TEXT NOT NULL DEFAULT ''")
    # 老表 code 列带 UNIQUE（ALTER 无法移除）→ 需要散列化的存量明文和多次清空都会撞约束；
    # 检测到唯一索引时整表重建为新形态（散列+提示位，无唯一约束），数据全保留
    import hashlib

    def _ch(code: str) -> str:
        return hashlib.sha256(code.strip().upper().encode()).hexdigest()

    indexes = conn.execute("PRAGMA index_list(invites)").fetchall()
    unique_on_code = False
    for idx in indexes:
        if not idx["unique"]:
            continue
        idx_cols = {r["name"] for r in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()}
        if idx_cols == {"code"}:
            unique_on_code = True
    legacy_plain = conn.execute("SELECT count(*) FROM invites WHERE code != ''").fetchone()[0]
    if unique_on_code or legacy_plain:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE invites_mig ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " member_id INTEGER NOT NULL,"
            " code TEXT NOT NULL DEFAULT '',"
            " code_hash TEXT NOT NULL DEFAULT '',"
            " code_hint TEXT NOT NULL DEFAULT '',"
            " created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
        )
        rows = conn.execute("SELECT * FROM invites").fetchall()
        for r in rows:
            if r["code"]:
                code_hash, hint = _ch(r["code"]), (r["code_hint"] or r["code"][-2:])
            else:
                code_hash, hint = r["code_hash"] or "", r["code_hint"] or ""
            if not code_hash:
                continue  # 残缺行（无明文无散列）无法挽救，跳过
            cur.execute(
                "INSERT INTO invites_mig(member_id, code, code_hash, code_hint, created_at)"
                " VALUES(?,?,?,?,?)",
                (r["member_id"], "", code_hash, hint, r["created_at"]),
            )
        cur.execute("DROP TABLE invites")
        cur.execute("ALTER TABLE invites_mig RENAME TO invites")
    # 活动多负责人：从旧的单负责人字段迁移；此后以 activity_leads 为准
    conn.execute(
        "INSERT OR IGNORE INTO activity_leads(activity_id, member_id)"
        " SELECT id, lead_id FROM activities WHERE lead_id IS NOT NULL"
    )
    # QQ 群化（2026-10-17）：旧「站长」(owner) 整体转「管理员」(admin)，原站长（id 最大者）接任新站长。
    # 用 settings 标记保证幂等；之后 owner/admin 语义不变。
    migrated = conn.execute(
        "SELECT 1 FROM settings WHERE key='qq_roles_migrated'"
    ).fetchone()
    if not migrated:
        legacy_owners = conn.execute(
            "SELECT id FROM members WHERE role='owner' ORDER BY id"
        ).fetchall()
        if legacy_owners:
            conn.execute("UPDATE members SET role='admin' WHERE role='owner'")
            conn.execute("UPDATE members SET role='owner' WHERE id=?", (legacy_owners[-1]["id"],))
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('qq_roles_migrated', '1')"
            " ON CONFLICT(key) DO UPDATE SET value='1'"
        )
    _ensure_owner(conn)
    _ensure_super(conn)
    # 旧 archive_jobs（专用表）并入通用 jobs 后弃用
    legacy = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='archive_jobs'").fetchone()
    if legacy:
        conn.execute(
            "INSERT INTO jobs(kind, status, ref_id, label, result, error, op_id, created_at, updated_at)"
            " SELECT 'archive_article', status, activity_id, title,"
            "        json_object('title', title), error, op_id, created_at, updated_at FROM archive_jobs"
        )
        conn.execute("DROP TABLE archive_jobs")
    # 日志脱敏：历史超管操作曾以系统账号原始名落库，统一改为「超管」
    # （名字以代码内拼接构造，避免源码中出现部署环境的历史账号名）
    legacy_super_name = "M" + "LING"
    conn.execute(
        "UPDATE activity_log SET actor_name='超管' WHERE actor_name=?", (legacy_super_name,)
    )
    # 作业不跨进程存活：启动时把上次未完成的标为失败，前端据实提示
    conn.execute(
        "UPDATE jobs SET status='failed', error='服务重启导致中断，请重试',"
        " updated_at=datetime('now','localtime') WHERE status IN ('pending','running')"
    )
    conn.execute("DELETE FROM jobs WHERE created_at < datetime('now','localtime', '-7 days')")
    sync_published_materials(conn)
    backfill_logs(conn)


def _ensure_super(conn) -> None:
    """内置隐藏超管：以 is_system 标记识别（不依赖姓名），固定邀请码只绑该账号且唯一。

    - 每次启动以当前 SUPER_CODE 的 HMAC 重建固定码：轮换即吊销旧码
    - 任何其它超管/系统身份一律回收，系统行不可经名册接口改名或删除
    """
    from .codes import code_hash

    row = conn.execute("SELECT id FROM members WHERE is_system=1 ORDER BY id LIMIT 1").fetchone()
    if row is None:
        row = conn.execute("SELECT id FROM members WHERE role='super' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO members(name, contact, role_note, active, role, is_system)"
            " VALUES('内置超管', '', '内置超级管理员', 1, 'super', 1)"
        )
        member_id = cur.lastrowid
    else:
        member_id = row["id"]
    conn.execute("UPDATE members SET role='super', active=1, is_system=1 WHERE id=?", (member_id,))
    conn.execute(
        "UPDATE members SET role='member', is_system=0"
        " WHERE (role='super' OR is_system=1) AND id!=?", (member_id,)
    )
    # 固定邀请码：只保留当前 SUPER_CODE 对应的一条（旧码轮换即失效）
    conn.execute("DELETE FROM invites WHERE member_id=?", (member_id,))
    conn.execute(
        "INSERT INTO invites(member_id, code, code_hash, code_hint) VALUES(?, '', ?, '')",
        (member_id, code_hash(SUPER_CODE)),
    )


def _ensure_owner(conn) -> None:
    """确保名册里存在站长；没有站长时先升最早在册的管理员接任。
    QQ 群主语义下站长只能经转让获得，普通成员不自动接任（避免把不经意的成员写成站长）。"""
    if conn.execute("SELECT 1 FROM members WHERE role='owner'").fetchone() is None:
        conn.execute(
            "UPDATE members SET role='owner'"
            " WHERE id=(SELECT id FROM members WHERE active=1 AND role='admin' ORDER BY id LIMIT 1)"
        )


def log_action(conn, detail: str, *, actor_id=None, activity_id=None, action="", created_at: str | None = None) -> None:
    """站点操作流水：谁（本机身份 actor_id）对哪个活动做了什么（detail 人话）。"""
    name = ""
    if actor_id:
        row = conn.execute("SELECT name, role FROM members WHERE id=?", (actor_id,)).fetchone()
        name = "超管" if (row and row["role"] == "super") else (row["name"] if row else "")
    cur = conn.execute(
        "INSERT INTO activity_log(actor_id, actor_name, activity_id, action, detail) VALUES(?,?,?,?,?)",
        (actor_id, name, activity_id, action, detail),
    )
    if created_at:
        # 历史回填：把这条记录改成真实发生的时间点
        conn.execute("UPDATE activity_log SET created_at=? WHERE id=?", (created_at, cur.lastrowid))


def backfill_logs(conn) -> None:
    """操作记录启用前的存量，从真实时间戳（创建/确认/通讯稿/归档）逐项补录。
    每个事件按其 action 的记录行是否已存在判断，天然幂等。
    ops_backfill_off 置位（人工清空操作记录后将不再重建历史）时跳过。"""
    if db_get_setting(conn, "ops_backfill_off"):
        return
    rows = conn.execute(
        "SELECT id, name, created_at, confirmed_at, published_at, report_updated_at FROM activities"
    ).fetchall()
    have = {
        (r["activity_id"], r["action"])
        for r in conn.execute("SELECT activity_id, action FROM activity_log").fetchall()
    }
    for a in rows:
        events = [
            ("create", f"创建了活动「{a['name']}」", a["created_at"]),
        ]
        if a["confirmed_at"]:
            events.append(("confirm", f"「{a['name']}」的负责人确认了接单", a["confirmed_at"]))
        if a["report_updated_at"] and (a["id"], "edit") not in have:
            events.append(("report", f"更新了「{a['name']}」的通讯稿", a["report_updated_at"]))
        if a["published_at"]:
            events.append(("publish", f"完成了「{a['name']}」的项目归档（推文已发布）", a["published_at"]))
        for action, detail, ts in events:
            if not ts or (a["id"], action) in have:
                continue
            conn.execute(
                "INSERT INTO activity_log(actor_id, actor_name, activity_id, action, detail, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (None, "", a["id"], action, detail, ts),
            )


def db_get_setting(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


def set_db_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, value))


def upsert_material_from_activity(conn, act):
    """已发布活动归档后，把推文自动收进素材库，供后续活动复用。

    以 article_url（source_url）为唯一键：首次插入（inserted=True），重复发布只更新正文等字段
    （inserted=False），不重复入册、不动已提取的要点字段。
    返回 (mid, inserted)；mid=None 表示没有文章链接、未入库。"""
    url = (act["article_url"] or "").strip()
    if not url:
        return None, False
    name = act["name"]
    source_title = act["article_title"] or name
    activity_type = act["activity_type"] or ""
    time_at = act["published_at"] or act["event_date"] or ""
    raw_text = act["article_text"] or ""
    existing = conn.execute(
        "SELECT id FROM materials WHERE source_url=? ORDER BY id LIMIT 1", (url,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE materials SET name=?, source_title=?, activity_type=?, time=?,"
            " raw_text=? WHERE id=?",
            (name, source_title, activity_type, time_at, raw_text, existing["id"]),
        )
        return existing["id"], False
    conn.execute(
        "INSERT INTO materials(name, source_title, source_url, activity_type, time, raw_text)"
        " VALUES(?,?,?,?,?,?)",
        (name, source_title, url, activity_type, time_at, raw_text),
    )
    return conn.execute(
        "SELECT id FROM materials WHERE source_url=? ORDER BY id LIMIT 1", (url,)
    ).fetchone()["id"], True


def sync_published_materials(conn) -> None:
    """存量已发布活动的推文回填素材库（幂等，source_url 去重）。"""
    rows = conn.execute(
        "SELECT * FROM activities WHERE published_at IS NOT NULL AND article_url!=''"
    ).fetchall()
    for act in rows:
        upsert_material_from_activity(conn, act)


def _env_override(key: str) -> str:
    name = ENV_OVERRIDES.get(key)
    return os.environ.get(name, "").strip() if name else ""


def get_setting(key: str, default: str | None = None) -> str | None:
    env_val = _env_override(key)
    if env_val:
        return env_val
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def key_source(key: str) -> str:
    """密钥类设置的实际来源：env（环境变量覆盖）/ db / unset。"""
    if _env_override(key):
        return "env"
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return "db" if row and row["value"] else "unset"


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
