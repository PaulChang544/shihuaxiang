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
    r = client.post("/api/materials/extract", json={"text": "足够长的推文正文内容用于测试字段归一化处理", "url": "https://mp.weixin.qq.com/s/y"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    data = client.get("/api/materials").json()[0]
    assert data["name"] == "建党日主题党日、研讨活动"
    assert data["form_points"] == "重温入党誓词、专题研讨、合影留念"
    assert "形式：庄重" in data["highlights"]
    assert data["participation"] == ""


def test_extract_from_screenshot(client, monkeypatch):
    """上传推文长截图：视觉转写正文 → AI 提取入库，来源标题用识别出的文章标题。"""
    import io
    import json

    from backend.app import llm
    from backend.tests.test_uploads_api import _real_png

    calls = {"n": 0}

    async def fake_chat(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # 首片：标题 + 正文转写
            return json.dumps({
                "is_article": True,
                "title": "中秋游园会回顾推文",
                "text": "昨天的中秋活动，居民们一起猜灯谜、做月饼、投壶，热闹非凡……",
            }, ensure_ascii=False)
        return json.dumps(MATERIAL_JSON, ensure_ascii=False)  # 素材字段提取

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(
        "/api/materials/extract-image",
        files={"file": ("shot.png", io.BytesIO(_real_png(8, 8)), "image/png")},
        data={"url": "https://mp.weixin.qq.com/s/abc"},
    )
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    data = client.get("/api/materials").json()[0]
    assert data["name"] == "中秋游园会"
    assert data["source_title"] == "中秋游园会回顾推文"
    assert data["source_url"] == "https://mp.weixin.qq.com/s/abc"
    assert "猜灯谜" in data["raw_text"]


def test_extract_from_screenshot_rejects_non_article(client, monkeypatch):
    import io
    import json

    from backend.app import llm
    from backend.tests.test_uploads_api import _real_png

    async def fake_chat(messages, **kwargs):
        return json.dumps({"is_article": False, "title": "", "text": ""}, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(
        "/api/materials/extract-image",
        files={"file": ("chat.png", io.BytesIO(_real_png(8, 8)), "image/png")},
        data={"url": "https://mp.weixin.qq.com/s/abc"},
    )
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed"
    assert "不是推文页面" in job["error"]
    assert client.get("/api/materials").json() == []


def test_extract_from_screenshot_validates_file(client):
    import io

    from backend.tests.test_uploads_api import _real_png

    r = client.post(
        "/api/materials/extract-image",
        files={"file": ("shot.txt", io.BytesIO(_real_png(8, 8)), "text/plain")},
        data={"url": "https://mp.weixin.qq.com/s/abc"},
    )
    assert r.status_code == 400
    r = client.post(
        "/api/materials/extract-image",
        files={"file": ("shot.png", io.BytesIO(b""), "image/png")},
        data={"url": "https://mp.weixin.qq.com/s/abc"},
    )
    assert r.status_code == 400


def test_url_required_for_intake(client, monkeypatch):
    """推文链接必填（与活动归档一致）：截图与粘贴正文两条入库路径都要先给链接。"""
    import io

    from backend.tests.test_uploads_api import _real_png

    r = client.post(
        "/api/materials/extract-image",
        files={"file": ("shot.png", io.BytesIO(_real_png(8, 8)), "image/png")},
    )
    assert r.status_code == 400 and "链接" in r.json()["detail"]
    r = client.post("/api/materials/extract", json={"text": "足够长的推文正文内容用于测试链接必填校验规则"})
    assert r.status_code == 400 and "链接" in r.json()["detail"]


def test_url_fetch_endpoint_removed(client):
    """全站不再抓取微信文章：抓取模块与旧接口都已移除。"""
    import importlib.util

    assert importlib.util.find_spec("backend.app.wechat") is None
    assert client.post(
        "/api/materials/extract-from-url", json={"url": "https://mp.weixin.qq.com/s/abc"}
    ).status_code in (404, 405)
