import json

IDEAS = {"ideas": [
    {"name": "国潮中秋游园会", "highlight": "跟着短视频学投壶", "hotword": "国潮",
     "hotword_source": "抖音", "activity_type": "节气时令", "feasibility": "物料简单", "star_rating": 5},
    {"name": "手工漆扇", "highlight": "非遗漆扇体验", "hotword": "漆扇",
     "hotword_source": "小红书", "activity_type": "手工 DIY", "feasibility": "成本低", "star_rating": 4},
]}


def _patch_chat_ok(monkeypatch):
    from backend.app import llm

    async def fake_chat(messages, **kwargs):
        return json.dumps(IDEAS, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)


def test_generate_ideas(client, monkeypatch):
    _patch_chat_ok(monkeypatch)
    r = client.post("/api/ideas/generate", json={"activity_types": ["节气时令", "手工 DIY"]})
    assert r.status_code == 200
    data = r.json()
    assert data["degraded"] is False
    assert len(data["ideas"]) == 2
    assert data["ideas"][0]["name"] == "国潮中秋游园会"
    assert data["ideas"][0]["id"] > 0
    # 落库
    assert len(client.get("/api/ideas").json()) == 2


def test_generate_degrades_when_search_unavailable(client, monkeypatch):
    from backend.app.agent import search as search_mod

    async def fake_search(query, **kwargs):
        raise search_mod.SearchUnavailable("未配置 DeepSeek API Key")

    monkeypatch.setattr(search_mod, "search_web", fake_search)
    _patch_chat_ok(monkeypatch)
    from backend.app import db
    db.set_setting("web_search", "1")
    r = client.post("/api/ideas/generate", json={"activity_types": ["茶话会"]})
    assert r.status_code == 200
    assert r.json()["degraded"] is True


def test_generate_injects_search_context(client, monkeypatch):
    from backend.app.agent import search as search_mod

    async def fake_search(query, **kwargs):
        assert "茶话会" in query
        return [{"title": "秋日围炉茶话会走红", "snippet": "年轻人流行围炉煮茶", "link": "https://x.example.com",
                 "media": "公众号", "date": "2026-10-01"}]

    monkeypatch.setattr(search_mod, "search_web", fake_search)
    from backend.app import llm
    seen = {}

    async def fake_chat(messages, **kwargs):
        seen["content"] = messages[-1].content
        return json.dumps(IDEAS, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    from backend.app import db
    db.set_setting("web_search", "1")
    r = client.post("/api/ideas/generate", json={"activity_types": ["茶话会"]})
    assert r.status_code == 200
    assert r.json()["degraded"] is False
    assert "围炉煮茶" in seen["content"]


def test_generate_bad_json_retries_once(client, monkeypatch):
    from backend.app import llm

    calls = {"n": 0}

    async def fake_chat(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "抱歉，这是文字说明"
        return json.dumps(IDEAS, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post("/api/ideas/generate", json={"activity_types": ["运动健身"]})
    assert r.status_code == 200
    assert calls["n"] == 2


def test_favorite_toggle(client, monkeypatch):
    _patch_chat_ok(monkeypatch)
    data = client.post("/api/ideas/generate", json={"activity_types": ["亲子"]}).json()
    iid = data["ideas"][0]["id"]
    assert client.post(f"/api/ideas/{iid}/favorite").json()["favorited"] is True
    assert client.get("/api/ideas?favorited=1").json()[0]["id"] == iid
    assert client.post(f"/api/ideas/{iid}/favorite").json()["favorited"] is False


def test_generate_requires_types(client):
    r = client.post("/api/ideas/generate", json={"activity_types": []})
    assert r.status_code == 400


def _parse_sse_events(text: str):
    events = []
    for block in text.split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if event and data:
            events.append((event, json.loads(data)))
    return events


def _fake_stream_gen(chunks, *, search=False):
    async def _gen(messages, **kwargs):
        if search:
            yield {"type": "search", "status": "searching"}
        for c in chunks:
            yield {"type": "text", "text": c}
        if search:
            yield {"type": "search", "status": "done"}
        yield {"type": "done", "text": "".join(chunks)}
    return _gen


def test_generate_stream_success(client, monkeypatch):
    from backend.app import llm
    monkeypatch.setattr(llm, "chat_stream", _fake_stream_gen([json.dumps(IDEAS, ensure_ascii=False)]))

    with client.stream("POST", "/api/ideas/generate/stream", json={"activity_types": ["节气时令"]}) as r:
        assert r.status_code == 200
        body = r.read().decode()

    events = _parse_sse_events(body)
    kinds = [e for e, _ in events]
    assert "step" in kinds and "chunk" in kinds
    final = [d for e, d in events if e == "final"][0]
    assert final["degraded"] is False
    assert len(final["ideas"]) == 2
    assert len(client.get("/api/ideas").json()) >= 2
    steps = [d for e, d in events if e == "step"]
    keys = {s["key"] for s in steps}
    assert {"config", "materials", "search", "generate", "parse", "save"} <= keys
    # 未开启联网时，检索步骤如实说明
    search_steps = [s for s in steps if s.get("key") == "search"]
    assert search_steps[-1]["detail"] and "未开启" in search_steps[-1]["detail"]


HOTWORDS = {"hotwords": [
    {"word": "捡秋", "platform": "小红书", "desc": "捡落叶做标本", "fit": "节气时令"},
    {"word": "Citywalk", "platform": "微博", "desc": "城市漫步打卡", "fit": "运动健身"},
]}


def _two_phase_gen(calls):
    """第 1 次调用返回热词（prompt 已注入检索上下文），第 2 次返回点子。"""
    async def _gen(messages, **kwargs):
        calls.append({"messages": messages})
        out = json.dumps(HOTWORDS, ensure_ascii=False) if len(calls) == 1 else json.dumps(IDEAS, ensure_ascii=False)
        for c in [out]:
            yield {"type": "text", "text": c}
        yield {"type": "done", "text": out}
    return _gen


def test_generate_stream_hotwords_before_generation(client, monkeypatch):
    from backend.app import db, llm
    from backend.app.agent import search as search_mod

    async def fake_search(query, **kwargs):
        assert "节气时令" in query
        return [{"title": "捡秋走红", "snippet": "捡落叶做标本刷屏小红书", "link": "https://x.example.com",
                 "media": "小红书", "date": "2026-10-10"}]

    monkeypatch.setattr(search_mod, "search_web", fake_search)
    db.set_setting("web_search", "1")
    calls = []
    monkeypatch.setattr(llm, "chat_stream", _two_phase_gen(calls))

    with client.stream("POST", "/api/ideas/generate/stream", json={"activity_types": ["节气时令"]}) as r:
        body = r.read().decode()

    events = _parse_sse_events(body)
    final = [d for e, d in events if e == "final"][0]
    assert final["degraded"] is False
    assert len(final["ideas"]) == 2
    assert len(calls) == 2
    # 热词调用的 prompt 末尾注入了真实检索结果；点子调用基于热词产出
    assert "捡秋走红" in "".join(calls[0]["messages"][-1].content)
    assert "捡秋" in "".join(calls[1]["messages"][-1].content)
    steps = [d for e, d in events if e == "step"]
    search_steps = [s for s in steps if s.get("key") == "search"]
    assert search_steps and search_steps[-1]["status"] == "done"
    assert any("提炼出 2 个热词" in (s.get("detail") or "") for s in search_steps)


def test_generate_stream_search_unavailable_degrades(client, monkeypatch):
    from backend.app import db, llm
    from backend.app.agent import search as search_mod

    async def fake_search(query, **kwargs):
        raise search_mod.SearchUnavailable("搜索失败（402）：余额不足")

    monkeypatch.setattr(search_mod, "search_web", fake_search)
    db.set_setting("web_search", "1")
    calls = []

    async def _gen(messages, **kwargs):
        calls.append(1)
        out = json.dumps(IDEAS, ensure_ascii=False)
        for c in [out]:
            yield {"type": "text", "text": c}
        yield {"type": "done", "text": out}

    monkeypatch.setattr(llm, "chat_stream", _gen)

    with client.stream("POST", "/api/ideas/generate/stream", json={"activity_types": ["茶话会"]}) as r:
        body = r.read().decode()

    events = _parse_sse_events(body)
    final = [d for e, d in events if e == "final"][0]
    assert final["degraded"] is True
    assert len(final["ideas"]) == 2
    assert len(calls) == 1  # 检索失败后跳过热词步骤，直接按素材生成
    steps = [d for e, d in events if e == "step"]
    search_steps = [s for s in steps if s.get("key") == "search"]
    assert search_steps[-1]["detail"] and "联网检索未成功" in search_steps[-1]["detail"]


def test_generate_stream_no_web_off(client, monkeypatch):
    from backend.app import llm
    monkeypatch.setattr(llm, "chat_stream", _fake_stream_gen([json.dumps(IDEAS, ensure_ascii=False)]))

    with client.stream("POST", "/api/ideas/generate/stream", json={"activity_types": ["节气时令"]}) as r:
        body = r.read().decode()

    events = _parse_sse_events(body)
    final = [d for e, d in events if e == "final"][0]
    assert final["degraded"] is False
    steps = [d for e, d in events if e == "step"]
    search_steps = [s for s in steps if s.get("key") == "search"]
    assert search_steps and "未开启" in search_steps[-1]["detail"]


def test_generate_stream_bad_json_retry(client, monkeypatch):
    from backend.app import llm
    calls = {"n": 0}

    async def _gen(messages, **kwargs):
        calls["n"] += 1
        text = "这不是JSON" if calls["n"] == 1 else json.dumps(IDEAS, ensure_ascii=False)
        yield {"type": "text", "text": text}
        yield {"type": "done", "text": text}

    monkeypatch.setattr(llm, "chat_stream", _gen)

    with client.stream("POST", "/api/ideas/generate/stream", json={"activity_types": ["运动健身"]}) as r:
        body = r.read().decode()

    events = _parse_sse_events(body)
    final = [d for e, d in events if e == "final"][0]
    assert len(final["ideas"]) == 2
    assert calls["n"] == 2
    assert any(s.get("key") == "retry" for e, s in events if e == "step")


def test_generate_stream_requires_types(client):
    with client.stream("POST", "/api/ideas/generate/stream", json={"activity_types": []}) as r:
        assert r.status_code == 400


def test_hotword_prompt_contains_today_and_types():
    from backend.app import prompts
    msgs = prompts.build_hotword_messages(["节气时令"])
    assert "hotwords" in msgs[1].content
    assert "节气时令" in msgs[1].content


def test_idea_prompt_injects_hotwords_text():
    from backend.app import prompts
    msgs = prompts.build_idea_messages(["节气时令"], {}, "", "- 捡秋（小红书）：捡落叶")
    assert "捡秋" in msgs[1].content
    msgs2 = prompts.build_idea_messages(["节气时令"], {}, "")
    assert "未联网检索" in msgs2[1].content


def test_hotword_prompt_covers_bilibili_and_zhihu():
    from backend.app import prompts
    content = prompts.build_hotword_messages(["校庆活动"])[1].content
    assert "B站" in content and "知乎" in content


def test_idea_prompt_restricts_to_venue():
    from backend.app import prompts
    content = prompts.build_idea_messages(["手工 DIY"], {}, "")[1].content
    assert "圆桌不可移动" in content
    assert "10 人以内" in content


def test_idea_prompt_carries_style_fingerprint_and_past_names():
    from backend.app import prompts
    past = ["小得盈满，心绘美好小满节气活动", "初见·慢聊小聚"]
    content = prompts.build_idea_messages(
        ["节气时令"], {}, "", past_names=past
    )[1].content
    for must in ["我与导师面对面", "明学沙龙", "对仗诗意", "暮秋已别",
                 "避免重复", "CCD 相机", "平均提分"]:
        assert must in content, f"点子提示词缺少风格指纹：{must}"
    assert "小得盈满" in content  # 已办清单注入


def test_list_past_names_limits(tmp_path, monkeypatch):
    from backend.app import db, materials
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as conn:
        for i in range(6):
            conn.execute("INSERT INTO materials(name) VALUES(?)", (f"活动{i}",))
        names = materials.list_past_names(conn, limit=3)
    assert len(names) == 3
