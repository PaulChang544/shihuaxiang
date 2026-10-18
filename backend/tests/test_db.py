import sqlite3


def test_init_db_creates_tables(tmp_path, monkeypatch):
    from backend.app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    conn = db.get_conn()
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "ideas", "materials", "settings", "chat_sessions", "chat_messages",
        "members", "activities", "expenses", "uploads",
    } <= names


def test_init_db_idempotent(tmp_path, monkeypatch):
    from backend.app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.init_db()  # 不应抛异常


def test_settings_roundtrip(tmp_path, monkeypatch):
    from backend.app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    assert db.get_setting("api_key") is None
    db.set_setting("api_key", "sk-1")
    assert db.get_setting("api_key") == "sk-1"
    db.set_setting("api_key", "sk-2")
    assert db.get_setting("api_key") == "sk-2"


def test_activity_and_chat_schema(tmp_path, monkeypatch):
    from backend.app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.init_db()  # 幂等
    with db.get_conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(activities)")}
        assert {"stage", "plan_note", "report_text", "article_url", "published_at", "event_date"} <= cols
        scols = {r["name"] for r in c.execute("PRAGMA table_info(chat_sessions)")}
        assert "brief" in scols  # 需求卡列包含在建表语句中，无需迁移
        row = c.execute("INSERT INTO chat_sessions(title) VALUES('测试') RETURNING id, status, title_locked").fetchone()
        assert row["status"] == "active" and row["title_locked"] == 0


def test_roles_migration_from_legacy_owners(tmp_path, monkeypatch):
    """QQ 群化迁移：旧体系的多位「站长」(owner) → 全员「管理员」，id 最大者接任唯一新站长；幂等。"""
    from backend.app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.get_conn() as c:
        # 构造旧体系：两位 owner（模拟生产库现状）+ 普通成员
        c.execute("UPDATE members SET role='owner' WHERE id=1")  # 最早引导成员
        c.execute("INSERT INTO members(name, contact, role_note, active, role) VALUES('旧站长乙', '', '站长', 1, 'owner')")
        c.execute("INSERT INTO members(name, contact, role_note, active) VALUES('组员丙', '', '宣发部', 1)")
        c.execute("DELETE FROM settings WHERE key='qq_roles_migrated'")  # 模拟升级前的库
    db.init_db()
    with db.get_conn() as c:
        roles = {r["name"]: r["role"] for r in c.execute("SELECT name, role FROM members WHERE role != 'super'")}
        assert roles["旧站长乙"] == "owner"          # id 最大的旧 owner 接任唯一站长
        owners = [n for n, r in roles.items() if r == "owner"]
        assert owners == ["旧站长乙"]
        assert all(r == "admin" for n, r in roles.items() if r == "admin")
        # 幂等：重跑 init_db 不再洗牌
        db.init_db()
        roles2 = {r["name"]: r["role"] for r in c.execute("SELECT name, role FROM members WHERE role != 'super'")}
        assert roles2 == roles


def test_legacy_archive_jobs_migrates_to_jobs(tmp_path, monkeypatch):
    """旧专用表 archive_jobs 迁入通用 jobs 后弃用（幂等）。"""
    from backend.app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    db.init_db()
    with db.get_conn() as c:
        c.execute("DROP TABLE jobs")
        c.execute(
            "CREATE TABLE archive_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER NOT NULL,"
            " status TEXT NOT NULL DEFAULT 'pending', title TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',"
            " op_id INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),"
            " updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
        )
        c.execute("INSERT INTO archive_jobs(activity_id, status, title) VALUES(6, 'done', '旧标题')")
    db.init_db()
    db.init_db()  # 幂等
    with db.get_conn() as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='archive_jobs'").fetchone() is None
        row = c.execute("SELECT kind, ref_id, result FROM jobs").fetchone()
        assert row["kind"] == "archive_article" and row["ref_id"] == 6
        assert "旧标题" in row["result"]
