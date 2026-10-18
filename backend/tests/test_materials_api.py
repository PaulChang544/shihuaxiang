MATERIAL_JSON = {
    "name": "中秋游园会",
    "activity_type": "节气时令",
    "time": "2024-09-15",
    "form_points": "猜灯谜、做月饼、投壶",
    "highlights": "国风打卡墙",
    "participation": "约120人",
}


def _wait_job(client, jid: int, timeout: float = 8.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/jobs/{jid}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"素材作业 {jid} 超时未完成")


def test_extract_material(client, monkeypatch):
    from backend.app import llm

    async def fake_chat(messages, **kwargs):
        import json
        return json.dumps(MATERIAL_JSON, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post("/api/materials/extract", json={"text": "昨天的中秋活动，居民们一起猜灯谜、做月饼、投壶，热闹非凡……", "url": "https://mp.weixin.qq.com/s/x"})
    assert r.status_code == 200 and r.json()["job_id"]
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    data = [m for m in client.get("/api/materials").json() if m["source_url"] == "https://mp.weixin.qq.com/s/x"][0]
    assert data["name"] == "中秋游园会"
    assert data["id"] > 0


def test_list_update_delete_material(client):
    from backend.app import db
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO materials(source_title, name, activity_type) VALUES('推文A', '旧活动', '手工 DIY')"
        )
        mid = cur.lastrowid

    items = client.get("/api/materials").json()
    assert [i["name"] for i in items] == ["旧活动"]

    r = client.put(f"/api/materials/{mid}", json={"name": "改名", "highlights": "亮点X"})
    assert r.status_code == 200
    assert client.get("/api/materials").json()[0]["name"] == "改名"

    assert client.delete(f"/api/materials/{mid}").status_code == 200
    assert client.get("/api/materials").json() == []


def test_summarize_for_types(tmp_path, monkeypatch):
    from backend.app import db, materials
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO materials(name, activity_type, highlights) VALUES('游园会', '节气时令', '国风')"
        )
        assert "游园会" in materials.summarize_for_types(conn, ["节气时令"])
        assert materials.summarize_for_types(conn, ["运动健身"]) == ""


def test_extract_coerces_list_fields(client, monkeypatch):
    """AI 把字段返回成数组时，应拼接成字符串入库而不是报错。"""
    from backend.app import llm

    async def fake_chat(messages, **kwargs):
        import json
        return json.dumps({
            "name": ["建党日主题党日", "研讨活动"],
            "activity_type": "党团学习",
            "time": "2026-07-01",
            "form_points": ["重温入党誓词", "专题研讨", "合影留念"],
            "highlights": {"形式": "庄重", "氛围": "热烈"},
            "participation": None,
        }, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post("/api/materials/extract", json={"text": "足够长的推文正文内容用于测试字段归一化处理"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    data = client.get("/api/materials").json()[0]
    assert data["name"] == "建党日主题党日、研讨活动"
    assert data["form_points"] == "重温入党誓词、专题研讨、合影留念"
    assert "形式：庄重" in data["highlights"]
    assert data["participation"] == ""


def test_extract_from_url(client, monkeypatch):
    """粘贴微信文章链接：服务端抓取正文 → AI 提取入库，来源标题用抓取到的标题。"""
    from backend.app import llm, wechat

    async def fake_fetch(url):
        return {
            "title": "中秋游园会回顾推文",
            "author": "十号社区",
            "publish_time": "2024-09-16 12:00",
            "text": "昨天的中秋活动，居民们一起猜灯谜、做月饼、投壶，热闹非凡……",
            "images": [],
        }

    async def fake_chat(messages, **kwargs):
        import json
        return json.dumps(MATERIAL_JSON, ensure_ascii=False)

    monkeypatch.setattr(wechat, "fetch_article", fake_fetch)
    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post("/api/materials/extract-from-url", json={"url": "https://mp.weixin.qq.com/s/abc"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    data = client.get("/api/materials").json()[0]
    assert data["name"] == "中秋游园会"
    assert data["source_title"] == "中秋游园会回顾推文"
    assert data["source_url"] == "https://mp.weixin.qq.com/s/abc"


def test_extract_from_url_fetch_error_maps_502(client, monkeypatch):
    from backend.app import wechat

    async def bad_fetch(url):
        raise wechat.FetchError("无法解析正文（可能已删除/受限/需要验证），请改用手动粘贴正文")

    monkeypatch.setattr(wechat, "fetch_article", bad_fetch)
    r = client.post("/api/materials/extract-from-url", json={"url": "https://mp.weixin.qq.com/s/dead"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed"
    assert "手动粘贴" in job["error"]


def test_extract_from_url_requires_url(client):
    assert client.post("/api/materials/extract-from-url", json={"url": "  "}).status_code == 400
