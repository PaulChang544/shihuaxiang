import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离环境：数据库、上传、导出、wiki、LangGraph 检查点全部落在临时目录，
    测试绝不触碰仓库根下的 storage/ exports/ wiki/（防止误删真实照片与票据）。"""
    from backend.app import db

    root = tmp_path / "project"
    (root / "storage").mkdir(parents=True)
    monkeypatch.setattr(db, "ROOT", root)
    monkeypatch.setattr(db, "DB_PATH", root / "storage" / "storage.db")
    monkeypatch.setattr(db, "UPLOADS_PATH", root / "storage" / "uploads")
    db.init_db()

    # 路由内嵌的 wiki 重建维护真实目录；测试一律改为 no-op（专项测试自行接管）
    from backend.app.agent import wiki
    monkeypatch.setattr(wiki, "rebuild", lambda: None)
    monkeypatch.setattr(wiki, "WIKI_DIR", root / "wiki")

    from backend.app.agent import graph
    monkeypatch.setattr(graph, "CHECKPOINT_PATH", root / "storage" / "langgraph.db")

    # claim 限流是进程内内存状态：测试共用同一假 IP，逐测试重置避免互相误伤
    from backend.app.routers import members as members_router
    monkeypatch.setattr(members_router, "_CLAIM_LOG", {})
    monkeypatch.setattr(members_router, "_CLAIM_LOCK", {})

    from backend.app.main import app
    return app


@pytest.fixture()
def anon(env):
    """未登录客户端（无会话 Cookie）。"""
    return TestClient(env)


@pytest.fixture()
def client(env):
    """默认以超管身份登录的客户端（Cookie 会话）。"""
    from backend.app import db

    c = TestClient(env)
    r = c.post("/api/members/claim", json={"code": db.SUPER_CODE})
    assert r.status_code == 200, r.text
    return c
