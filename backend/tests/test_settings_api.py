import pytest


def _op(client):
    from backend.app import db
    return client.post('/api/members/claim', json={'code': db.SUPER_CODE}).json()['member']['id']



def test_get_settings_defaults(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert set(data) >= {"api_base", "api_key", "model", "api_mode", "web_search"}
    assert data["api_base"] == "https://api.deepseek.com"
    assert data["model"] == "deepseek-flash"
    assert data["api_mode"] == "responses"
    assert data["web_search"] in ("0", "1")


def test_put_settings_persists(client, tmp_path):
    r = client.put("/api/settings", json={"op_id": _op(client), "api_base": "https://x/v4", "api_key": "sk", "model": "m", "api_mode": "chat", "web_search": "1"})
    assert r.status_code == 200
    from backend.app import db
    assert db.get_setting("api_key") == "sk"
    assert db.get_setting("api_mode") == "chat"
    assert client.get("/api/settings").json()["api_base"] == "https://x/v4"


def test_put_settings_rejects_unknown_mode_and_blank_base(client):
    client.put("/api/settings", json={"op_id": _op(client), "api_base": "", "api_key": "sk", "model": "", "api_mode": "nonsense"})
    data = client.get("/api/settings").json()
    assert data["api_mode"] == "responses"
    assert data["api_base"] == "https://api.deepseek.com"
    assert data["model"] == "deepseek-flash"


def test_connection_check(client, monkeypatch):
    from backend.app import llm

    async def fake_chat(messages, **kwargs):
        return "ok"

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post("/api/settings/test", json={"op_id": _op(client)})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "ok"}


def test_keys_masked_in_get(client):
    client.put("/api/settings", json={"op_id": _op(client), "api_key": "sk-test1234567890"})
    r = client.get("/api/settings")
    data = r.json()
    assert data["api_key"] == "sk-te****7890"
    assert data["has_api_key"]
    assert "sk-test1234567890" not in r.text  # 完整 Key 绝不回传
    assert "has_zhipu_key" not in data  # 智谱独立 Key 已退役，联网检索复用主 Key


def test_put_empty_or_masked_keeps_key(client):
    from backend.app import db

    client.put("/api/settings", json={"op_id": _op(client), "api_key": "sk-test1234567890"})
    masked = client.get("/api/settings").json()["api_key"]
    client.put("/api/settings", json={"op_id": _op(client), "api_key": ""})  # 留空 = 保留
    assert db.get_setting("api_key") == "sk-test1234567890"
    client.put("/api/settings", json={"op_id": _op(client), "api_key": masked})  # 回传掩码 = 保留
    assert db.get_setting("api_key") == "sk-test1234567890"


def test_put_new_value_replaces_key(client):
    from backend.app import db

    client.put("/api/settings", json={"op_id": _op(client), "api_key": "sk-old12345"})
    client.put("/api/settings", json={"op_id": _op(client), "api_key": "sk-new67890"})
    assert db.get_setting("api_key") == "sk-new67890"


def test_clear_flag_removes_key(client):
    from backend.app import db

    client.put("/api/settings", json={"op_id": _op(client), "api_key": "sk-test1234567890"})
    client.put("/api/settings", json={"op_id": _op(client), "api_key": "", "clear_api_key": True})
    assert not db.get_setting("api_key")
    assert client.get("/api/settings").json()["has_api_key"] is False


def test_env_overrides_db_keys(client, monkeypatch):
    from backend.app import db

    client.put("/api/settings", json={"op_id": _op(client), "api_key": "sk-from-db123456"})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env123456")
    data = client.get("/api/settings").json()
    assert data["api_key"] == "sk-fr****3456"
    assert data["api_key_source"] == "env"
    assert db.get_setting("api_key") == "sk-from-env123456"  # 运行时取值走环境变量
    monkeypatch.delenv("DEEPSEEK_API_KEY")  # 移除后回落数据库
    assert db.get_setting("api_key") == "sk-from-db123456"
    assert client.get("/api/settings").json()["api_key_source"] == "db"


def test_settings_super_admin_only(client, env):
    """AI 设置仅超管会话可用：站长/管理员/成员一律 403（身份来自会话，不再是 op_id）。"""
    from fastapi.testclient import TestClient

    owner = client.post("/api/members", json={"name": "站长甲"}).json()
    code = client.post(f"/api/members/{owner['id']}/invites").json()["code"]
    owner_c = TestClient(env)
    assert owner_c.post("/api/members/claim", json={"code": code}).status_code == 200

    assert owner_c.get("/api/settings").status_code == 403
    assert owner_c.put("/api/settings", json={"api_base": "https://x/v4"}).status_code == 403
    assert owner_c.post("/api/settings/test").status_code == 403

    assert client.put("/api/settings", json={"api_base": "https://x/v4"}).status_code == 200
    assert client.post("/api/settings/test").status_code in (200, 502)

