import io
import json
import struct
import zlib

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50


def _real_png(w: int, h: int) -> bytes:
    """可被 PIL 解码的真 PNG（白底 RGB），用于图片处理相关测试。"""
    def _chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))

    scanlines = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(scanlines))
            + _chunk(b"IEND", b""))


def _as(env, manager, mid):
    """以成员身份登录：管理员签发邀请码 → 新客户端 claim（切换会话身份）。"""
    from fastapi.testclient import TestClient
    code = manager.post(f"/api/members/{mid}/invites").json()["code"]
    c = TestClient(env)
    assert c.post("/api/members/claim", json={"code": code}).status_code == 200
    return c


def _wait_job(client, jid: int, timeout: float = 8.0):
    """轮询归档任务直到完成/失败（后台 asyncio 任务在 TestClient 的循环上推进）。"""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/jobs/{jid}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"归档任务 {jid} 超时未完成")


def _create(client, name="中秋游园会", atype="节气时令"):
    r = client.post("/api/activities", json={"name": name, "activity_type": atype})
    assert r.status_code == 200
    return r.json()


def test_full_lifecycle(client, monkeypatch, env):
    assert client.get("/api/activities").json() == []
    act = _create(client)
    aid = act["id"]
    assert act["stage"] == "dispatch" and act["dispatch_status"] == "draft"

    # 派发：可设多位负责人 → 待确认（普通成员即负责人）
    from backend.app import db as _db
    sup = client.post("/api/members/claim", json={"code": _db.SUPER_CODE}).json()["member"]
    member = client.post("/api/members", json={"name": "组织委员", "op_id": sup["id"]}).json()
    member2 = client.post("/api/members", json={"name": "协同委员", "op_id": sup["id"]}).json()
    detail = client.patch(
        f"/api/activities/{aid}", json={"lead_ids": [member["id"], member2["id"]]}
    ).json()
    assert detail["lead_ids"] == [member["id"], member2["id"]]
    assert member["name"] in detail["lead_name"] and "协同委员" in detail["lead_name"]
    assert detail["dispatch_status"] == "assigned"

    # 确认：非负责人被拒；越权成员 id 也被拒；任一负责人可确认
    other = client.post("/api/members", json={"name": "路人"}).json()
    other_c = _as(env, client, other["id"])
    assert other_c.post(f"/api/activities/{aid}/confirm").status_code == 403  # 非负责人
    assert client.post(f"/api/activities/{aid}/confirm").status_code == 403  # 超管也须本人是负责人
    member2_c = _as(env, client, member2["id"])
    detail = member2_c.post(f"/api/activities/{aid}/confirm").json()
    assert detail["dispatch_status"] == "confirmed"
    assert detail["confirmed_at"] and detail["stage"] == "planning"
    # 幂等：重复确认不报错
    assert member2_c.post(f"/api/activities/{aid}/confirm").json()["ok"] is True

    # 策划文本备注与基本信息
    detail = client.patch(
        f"/api/activities/{aid}",
        json={"plan_note": "流程：破冰→手作→分享", "event_date": "2026-10-20", "location": "党团活动室"},
    ).json()
    assert detail["plan_note"].startswith("流程") and detail["event_date"] == "2026-10-20"

    # 采购报销
    client.patch(f"/api/activities/{aid}", json={"stage": "procurement"})
    e1 = client.post(f"/api/activities/{aid}/expenses", json={"item": "扭扭棒材料包", "amount": 45.5}).json()
    client.post(f"/api/activities/{aid}/expenses", json={"item": "茶叶点心", "amount": 60})
    detail = client.get(f"/api/activities/{aid}").json()
    assert detail["expense_total"] == 105.5 and detail["expense_reimbursed"] == 0
    client.patch(f"/api/expenses/{e1['id']}", json={"item": "扭扭棒材料包", "amount": 45.5, "reimbursed": True})
    detail = client.get(f"/api/activities/{aid}").json()
    assert detail["expense_reimbursed"] == 45.5
    assert client.post(f"/api/activities/{aid}/expenses", json={"item": " "}).status_code == 400
    assert client.get(f"/api/activities/{aid}/expenses").status_code == 200

    # 票据上传
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("发票.jpg", io.BytesIO(_real_png(8, 8)), "image/jpeg")},
        data={"category": "receipt"},
    )
    assert r.status_code == 200

    # 举办信息
    client.patch(
        f"/api/activities/{aid}",
        json={"stage": "execution", "participants": "12 人", "summary_note": "顺利办完"},
    )

    # 影像稿件
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("合影.png", io.BytesIO(_real_png(8, 8)), "image/png")},
        data={"category": "photo", "caption": "大合影"},
    )
    assert r.status_code == 200
    detail = client.patch(f"/api/activities/{aid}", json={"stage": "coverage", "report_text": "金秋十月……"}).json()
    assert detail["report_updated_at"]

    # 归档：上传推文长截图 → 异步识别（mock 多模态）→ 轮询任务 → 标题与正文入库
    from backend.app import llm

    async def fake_chat(messages, **kwargs):
        return json.dumps({"is_article": True, "title": "中秋游园会回顾", "text": "现场气氛圆满……"},
                          ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(
        f"/api/activities/{aid}/archive-article-image",
        files={"file": ("推文长图.png", io.BytesIO(_real_png(8, 8)), "image/png")},
        data={"url": "https://mp.weixin.qq.com/s/x", "op_id": "1"},
    )
    assert r.status_code == 200 and r.json()["job_id"]
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["title"] == "中秋游园会回顾"
    detail = client.get(f"/api/activities/{aid}").json()
    assert detail["article_title"] == "中秋游园会回顾"
    assert detail["article_url"] == "https://mp.weixin.qq.com/s/x"
    assert not [u for u in detail["uploads"] if u["caption"] == "推文配图"], "文章装饰图不应进照片墙"

    # 推进到推文发布阶段（阶段守卫：publish-confirm 不允许跳级）
    detail = client.patch(f"/api/activities/{aid}", json={"stage": "publish"}).json()
    assert detail["stage"] == "publish"
    # 发布确认 → 已完成；归档推文自动收进素材库（幂等，重复确认不重复入册）
    detail = client.post(f"/api/activities/{aid}/publish-confirm").json()
    assert detail["stage"] == "done" and detail["published_at"]
    assert detail["material_synced"] is True
    mats = [m for m in client.get("/api/materials").json() if m["source_url"] == "https://mp.weixin.qq.com/s/x"]
    assert len(mats) == 1
    m = mats[0]
    assert m["name"] == "中秋游园会" and m["source_title"] == "中秋游园会回顾"
    assert m["activity_type"] == "节气时令" and m["raw_text"] == "现场气氛圆满……"
    assert client.post(f"/api/activities/{aid}/publish-confirm").json()["material_synced"] is False
    mats = client.get("/api/materials").json()
    assert len([m for m in mats if m["source_url"] == "https://mp.weixin.qq.com/s/x"]) == 1

    # 未归档就确认发布 → 400
    aid2 = _create(client)["id"]
    assert client.post(f"/api/activities/{aid2}/publish-confirm").status_code == 400


def test_unknown_stage_rejected(client):
    aid = _create(client)["id"]
    assert client.patch(f"/api/activities/{aid}", json={"stage": "nope"}).status_code == 400


def test_blank_name_rejected(client):
    aid = _create(client)["id"]
    r = client.patch(f"/api/activities/{aid}", json={"name": "  "})
    assert r.status_code == 400


def test_assign_lead_requires_member(client):
    aid = _create(client)["id"]
    r = client.patch(f"/api/activities/{aid}", json={"lead_ids": [12345]})
    assert r.status_code == 404
    # 空列表 = 清空负责人，回到待派发
    ok = client.patch(f"/api/activities/{aid}", json={"lead_ids": []}).json()
    assert ok["dispatch_status"] == "draft" and ok["lead_ids"] == []


def test_create_from_idea(client):
    from backend.app import db
    with db.get_conn() as conn:
        cur = conn.execute("INSERT INTO ideas(name, activity_type) VALUES('重阳敬老茶话会', '志愿服务')")
        iid = cur.lastrowid
    detail = client.post("/api/activities", json={"idea_id": iid}).json()
    assert detail["name"] == "重阳敬老茶话会"
    assert detail["activity_type"] == "志愿服务" and detail["idea_id"] == iid


def test_create_with_missing_idea(client):
    assert client.post("/api/activities", json={"idea_id": 42}).status_code == 404


def test_create_requires_name(client):
    assert client.post("/api/activities", json={"name": "  "}).status_code == 400
    assert client.post("/api/activities", json={}).status_code == 400


def test_delete_cascades(client):
    aid = _create(client)["id"]
    client.post(f"/api/activities/{aid}/expenses", json={"item": "材料", "amount": 10})
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("a.jpg", io.BytesIO(_real_png(8, 8)), "image/jpeg")},
    )
    stored = r.json()["stored_path"]
    assert client.delete(f"/api/activities/{aid}").json() == {"ok": True}
    assert client.get(f"/api/activities/{aid}").status_code == 404
    assert not (db_root() / stored).exists()
    assert client.get(f"/api/activities/{aid}/expenses").status_code == 404
    missing = client.get(f"/api/files/{r.json()['id']}")
    assert missing.status_code == 404


def db_root():
    from backend.app import db
    return db.ROOT


def test_expense_not_found(client):
    assert client.patch("/api/expenses/9999", json={"item": "x", "amount": 1}).status_code == 404
    assert client.delete("/api/expenses/9999").status_code == 404


# ---- DDL 与归档条目 ----

def _future(days):
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def test_create_sets_derived_ddls(client):
    event = _future(20)
    d = client.post("/api/activities", json={"name": "ddl 活动", "event_date": event}).json()
    assert d["event_date"] == event
    # 未显式填写：招募/策划=前7天，采购=前3天
    assert d["plan_ddl"] == _future(13)
    assert d["recruitment_ddl"] == _future(13)
    assert d["procurement_ddl"] == _future(17)


def test_create_ddl_explicit_overrides_derived(client):
    d = client.post("/api/activities", json={
        "name": "自定义", "event_date": _future(30),
        "plan_ddl": _future(10), "procurement_ddl": _future(5),
    }).json()
    assert d["plan_ddl"] == _future(10)
    assert d["procurement_ddl"] == _future(5)
    assert d["recruitment_ddl"] == _future(23)  # 未填的仍自动推导


def test_ddl_summary_ordered_and_skips_done(client):
    client.post("/api/activities", json={"name": "临近", "event_date": _future(8)})
    far = client.post("/api/activities", json={"name": "较远", "event_date": _future(40)}).json()
    client.post("/api/members", json={"name": "组织", "op_id": 1})  # 首位成员即站长（超管带头）
    lead_id = client.get("/api/members").json()[0]["id"]
    client.patch(
        f"/api/activities/{far['id']}",
        json={
            "lead_ids": [lead_id],
            "stage": "done",
            "plan_note": "要点",
            "report_text": "稿",
            "article_url": "https://mp.weixin.qq.com/s/z",
        },
    )
    items = client.get("/api/activities/ddl-summary").json()
    # 较远活动 stage=done 应被跳过；临近活动出 3 类 ddl（plan/recruitment/procurement）
    assert {i["name"] for i in items} == {"临近"}
    kinds = {i["kind"] for i in items if i["name"] == "临近"}
    assert kinds == {"plan", "recruitment", "procurement"}
    # 升序：剩余天数递增
    days = [i["days_left"] for i in items]
    assert days == sorted(days)
    plan = [i for i in items if i["kind"] == "plan"][0]
    assert plan["days_left"] == 1  # 举办日前 7 天 = 第 1 天（event=future(8)）


def test_detail_returns_ddl_items(client):
    d = client.post("/api/activities", json={"name": "摸底", "event_date": _future(10)}).json()
    detail = client.get(f"/api/activities/{d['id']}").json()
    kinds = {i["kind"] for i in detail["ddl_items"]}
    assert kinds == {"plan", "recruitment", "procurement"}
    assert all(i["activity_id"] == d["id"] for i in detail["ddl_items"])


def test_recruit_url_archivable(client):
    aid = _create(client)["id"]
    d = client.patch(f"/api/activities/{aid}", json={"recruitment_url": "https://mp.weixin.qq.com/s/recruit"}).json()
    assert d["recruitment_url"].startswith("https://")


def test_patch_event_date_fills_missing_ddls_only(client):
    d = client.post("/api/activities", json={"name": "后来定档"}).json()  # 无日期 → 无 ddl
    assert d["plan_ddl"] == ""
    aid = d["id"]
    d2 = client.patch(f"/api/activities/{aid}", json={"event_date": _future(21)}).json()
    assert d2["plan_ddl"] == _future(14) and d2["procurement_ddl"] == _future(18)
    # 显式填过的不再被覆盖
    client.patch(f"/api/activities/{aid}", json={"procurement_ddl": _future(1)})
    d3 = client.patch(f"/api/activities/{aid}", json={"event_date": _future(31)}).json()
    assert d3["plan_ddl"] == _future(24)
    assert d3["procurement_ddl"] == _future(1)  # 保持用户值


def test_patch_rejects_bad_date_format(client):
    aid = _create(client)["id"]
    r = client.patch(f"/api/activities/{aid}", json={"event_date": "下周一"})
    assert r.status_code == 400


# ---- 阶段推进门禁 ----

def test_stage_gates_forward(client):
    aid = _create(client)["id"]  # dispatch，无负责人
    # 直接进策划：无负责人被拒
    r = client.patch(f"/api/activities/{aid}", json={"stage": "planning"})
    assert r.status_code == 400 and "指派负责人" in r.json()["detail"]
    # 指派后放行
    member = client.post("/api/members", json={"name": "组织", "op_id": 1})  # 首位成员引导期为站长
    member = client.get("/api/members").json()[0]  # 引导后的第一位即站长
    client.patch(f"/api/activities/{aid}", json={"lead_ids": [member["id"]]})
    r = client.patch(f"/api/activities/{aid}", json={"stage": "planning"})
    assert r.status_code == 200

    # 采购：无方案要点被拒；同 patch 带要点即放行
    r = client.patch(f"/api/activities/{aid}", json={"stage": "procurement"})
    assert r.status_code == 400 and "方案要点" in r.json()["detail"]
    r = client.patch(f"/api/activities/{aid}", json={"stage": "procurement", "plan_note": "要点齐"})
    assert r.status_code == 200

    # 举办可静默跳过
    r = client.patch(f"/api/activities/{aid}", json={"stage": "execution"})
    assert r.status_code == 200

    # 推文发布：无通讯稿被拒；同 patch 带稿即放行；publish 本身上的 done 需文章链接
    r = client.patch(f"/api/activities/{aid}", json={"stage": "publish"})
    assert r.status_code == 400 and "通讯稿" in r.json()["detail"]
    r = client.patch(f"/api/activities/{aid}", json={"stage": "publish", "report_text": "金秋通讯稿"})
    assert r.status_code == 200
    r = client.patch(f"/api/activities/{aid}", json={"stage": "done"})
    assert r.status_code == 400 and "文章链接" in r.json()["detail"]


def test_stage_gate_publish_confirm_still_requires_article(client, env):
    aid = _create(client)["id"]
    client.post("/api/members", json={"name": "组织", "op_id": 1})  # 首位成员即站长（超管带头）
    member = client.get("/api/members").json()[0]  # 引导后的第一位即站长
    client.patch(f"/api/activities/{aid}", json={"lead_ids": [member["id"]]})
    _as(env, client, member["id"]).post(f"/api/activities/{aid}/confirm")
    client.patch(f"/api/activities/{aid}", json={"plan_note": "要点"})
    client.patch(f"/api/activities/{aid}", json={"stage": "procurement"})
    client.patch(f"/api/activities/{aid}", json={"stage": "execution"})
    client.patch(f"/api/activities/{aid}", json={"stage": "coverage"})
    client.patch(f"/api/activities/{aid}", json={"stage": "publish", "report_text": "稿"})
    r = client.post(f"/api/activities/{aid}/publish-confirm")
    assert r.status_code == 400 and "文章链接" in r.json()["detail"]


def test_stage_gates_allow_backward(client, env):
    aid = _create(client)["id"]
    client.post("/api/members", json={"name": "组织", "op_id": 1})  # 首位成员即站长（超管带头）
    member = client.get("/api/members").json()[0]  # 引导后的第一位即站长
    client.patch(f"/api/activities/{aid}", json={"lead_ids": [member["id"]]})
    _as(env, client, member["id"]).post(f"/api/activities/{aid}/confirm")
    client.patch(f"/api/activities/{aid}", json={"plan_note": "要点", "stage": "procurement"})
    client.patch(f"/api/activities/{aid}", json={"stage": "execution"})
    # 回退：从 execution 退回 procurement 再无限制修正
    r = client.patch(f"/api/activities/{aid}", json={"stage": "procurement"})
    assert r.status_code == 200
    r = client.patch(f"/api/activities/{aid}", json={"stage": "execution"})
    assert r.status_code == 200


def test_report_generate_and_docx_export(client, monkeypatch):
    """AI 生成通讯稿（mock LLM）与 Word 导出（含照片、PK 魔数）。"""
    act = client.post("/api/activities", json={"name": "小雪搓粘土", "activity_type": "节气时令", "event_date": "2026-11-22"}).json()
    aid = act["id"]
    # 真实 2x2 PNG（python-docx 嵌图要求可解析的图片头）
    import io as _io
    import struct as _struct
    import zlib as _zlib

    def _chunk(tag, payload):
        return _struct.pack(">I", len(payload)) + tag + payload + _struct.pack(">I", _zlib.crc32(tag + payload))

    _PNG = (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", _struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", _zlib.compress(bytes([0, 250, 200, 180, 0, 250, 200, 180, 0, 250, 200, 180, 0, 250, 200, 180])))
            + _chunk(b"IEND", b""))
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("现场.png", _io.BytesIO(_PNG), "image/png")},
        data={"category": "photo", "caption": "搓粘土现场"},
    )
    assert r.status_code == 200
    client.patch(f"/api/activities/{aid}", json={"plan_note": "计划三段式流程", "report_text": "旧稿"})

    # mock LLM：返回固定通讯稿
    from backend.app import llm

    async def fake_chat(messages, **kwargs):
        return "雪语盈盈，巧手成趣——小雪搓粘土活动圆满举办"

    monkeypatch.setattr(llm, "chat", fake_chat)
    # 通讯稿生成也是异步作业：提交 → 轮询 → 服务端自动保存
    r = client.post(f"/api/activities/{aid}/report/generate")
    assert r.status_code == 200 and r.json()["job_id"]
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["text"] == "雪语盈盈，巧手成趣——小雪搓粘土活动圆满举办"
    assert client.get(f"/api/activities/{aid}").json()["report_text"] == job["result"]["text"]

    # Word 导出：提交异步构建任务 → 轮询完成 → Range 断点下载
    from backend.app import db
    r = client.post(f"/api/activities/{aid}/export-docx", params={"op_id": 1})
    assert r.status_code == 200 and r.json()["job_id"]
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    meta = job["result"]
    with db.get_conn() as conn:  # 导出应把操作者记进操作记录
        actor = conn.execute(
            "SELECT actor_id FROM activity_log WHERE activity_id=? AND action='export' ORDER BY id DESC LIMIT 1",
            (aid,),
        ).fetchone()
        assert actor and actor["actor_id"] == 1
    assert meta["size"] > 4000 and meta["name"].endswith(".docx")
    r = client.get(f"/api/activities/{aid}/export-docx/download", params={"name": meta["name"]})
    assert r.status_code == 200 and r.headers["accept-ranges"] == "bytes"
    data = r.content
    assert data[:2] == b"PK" and len(data) == meta["size"]
    import zipfile
    z = zipfile.ZipFile(_io.BytesIO(data))
    assert any(n.startswith("word/media/") for n in z.namelist())
    body = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.startswith("word/document"))
    assert "小雪搓粘土" in body and "搓粘土现场" in body
    # Range 断点：取前 100 字节应为 206 且可拼回完整文件
    part = client.get(f"/api/activities/{aid}/export-docx/download",
                      params={"name": meta["name"]}, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.headers["content-range"].endswith(f"/{meta['size']}")
    rest = client.get(f"/api/activities/{aid}/export-docx/download",
                      params={"name": meta["name"]}, headers={"Range": f"bytes=100-"})
    assert rest.status_code == 206
    assert part.content + rest.content == data
    # exports/ 留档由导出路由写盘（名字含活动名与日期），验证后清掉测试残档
    leftover = list((db.ROOT / "exports").glob("小雪搓粘土-*.docx"))
    assert leftover, "导出应在 exports/ 落盘留档"
    for p in leftover:
        p.unlink()
    aid2 = client.post("/api/activities", json={"name": "空空如也", "event_date": "2026-12-01"}).json()["id"]
    job2 = _wait_job(client, client.post(f"/api/activities/{aid2}/export-docx").json()["job_id"])
    assert job2["status"] == "failed" and "通讯稿" in job2["error"]


def test_patch_event_date_writes_log(client):
    """全字段差异记录：日期修改必须入账（中文标签 + 值预览）。"""
    aid = client.post("/api/activities", json={"name": "小雪粘土", "event_date": "2026-11-22"}).json()["id"]
    r = client.patch(f"/api/activities/{aid}", json={"event_date": "2026-11-25", "location": "党团活动室", "op_id": 1})
    assert r.status_code == 200
    logs = client.get("/api/log").json()["items"]
    edit_row = [l for l in logs if l["action"] == "edit" and l["activity_id"] == aid][0]
    assert "举办日期 2026-11-25" in edit_row["detail"] and "地点 党团活动室" in edit_row["detail"]
    assert edit_row["actor_name"] == "超管"  # 超管操作统一记「超管」
    # 同值重复提交不重复入账
    n_before = len(client.get("/api/log").json()["items"])
    client.patch(f"/api/activities/{aid}", json={"event_date": "2026-11-25", "op_id": 1})
    assert len(client.get("/api/log").json()["items"]) == n_before


def test_log_backfill_from_real_timestamps(client, env):
    """存量活动的历史事件按真实时间戳补录一次（沿用既有数据可追溯）。"""
    from backend.app import db

    aid = client.post("/api/activities", json={"name": "冬至果壳风铃", "event_date": "2026-12-22"}).json()["id"]
    lead = client.post("/api/members", json={"name": "组织委员", "op_id": _op(client) if False else 1}).json()
    client.patch(f"/api/activities/{aid}", json={"lead_ids": [lead["id"]]})
    resp = _as(env, client, lead["id"]).post(f"/api/activities/{aid}/confirm")
    assert resp.status_code == 200
    client.patch(f"/api/activities/{aid}", json={"report_text": "风铃一篇"})
    # 直接置发布时间，走发布记录
    with db.get_conn() as conn:
        conn.execute("UPDATE activities SET published_at='2026-12-23 10:00:00', stage='done' WHERE id=?", (aid,))
    db.init_db()  # 触发 migrate → backfill（该活动尚无 create 记录，补录应执行且幂等）
    with db.get_conn() as conn:
        rows = [dict(r) for r in db.get_conn().execute(
            "SELECT action, detail, created_at FROM activity_log WHERE activity_id=? ORDER BY created_at, id", (aid,)
        ).fetchall()]
        db.init_db()  # 第二次调用：create 记录已在，天然幂等不再重复补录
        n_after = conn.execute("SELECT count(*) FROM activity_log WHERE activity_id=?", (aid,)).fetchone()[0]
        assert n_after == len(rows)
    actions = [r["action"] for r in rows]
    assert "create" in actions and "confirm" in actions and "publish" in actions
    create_row = [r for r in rows if r["action"] == "create"][0]
    assert "冬至果壳风铃" in create_row["detail"]


def test_ops_backfill_off_marker(client):
    """人工清空操作记录后置回填开关：迁移不再重建历史。"""
    from backend.app import db

    aid = client.post("/api/activities", json={"name": "X", "event_date": "2026-11-01"}).json()["id"]
    with db.get_conn() as conn:
        conn.execute("DELETE FROM activity_log")  # 模拟“清空操作记录”
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('ops_backfill_off','1')")
    db.init_db()  # 正常会补 create 行；标记存在则跳过
    with db.get_conn() as conn:
        n = conn.execute("SELECT count(*) FROM activity_log WHERE activity_id=?", (aid,)).fetchone()[0]
        assert n == 0


def test_publish_confirm_records_actor(client):
    """发布归档：op_id 走查询参数（不要 member_id），操作者应写进操作记录。"""
    from backend.app import db
    aid = client.post("/api/activities", json={"name": "归档署名", "event_date": "2026-12-05"}).json()["id"]
    with db.get_conn() as conn:
        conn.execute("UPDATE activities SET article_url='https://mp.weixin.qq.com/s/aaa',"
                     " stage='publish', report_text='稿' WHERE id=?", (aid,))
    r = client.post(f"/api/activities/{aid}/publish-confirm", params={"op_id": 1})
    assert r.status_code == 200
    d = r.json()
    assert d["stage"] == "done" and d["published_at"]
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT actor_id FROM activity_log WHERE activity_id=? AND action='publish'", (aid,)
        ).fetchone()
        assert row["actor_id"] == 1
    # 不带 op_id 也能归档（历史调用兼容）
    aid2 = client.post("/api/activities", json={"name": "无署名归档", "event_date": "2026-12-06"}).json()["id"]
    with db.get_conn() as conn:
        conn.execute("UPDATE activities SET article_url='https://mp.weixin.qq.com/s/bbb',"
                     " stage='publish', report_text='稿' WHERE id=?", (aid2,))
    r2 = client.post(f"/api/activities/{aid2}/publish-confirm")
    assert r2.status_code == 200 and r2.json()["stage"] == "done"


def test_archive_article_image_manual_fallback(client, monkeypatch):
    """截图归档：url 必填；提交即返回任务号，后台异步识别，轮询 done 后 article 字段落库并入操作记录。"""
    from backend.app import llm

    aid = client.post("/api/activities", json={"name": "截图归档活动"}).json()["id"]
    # 缺 url → 400
    r = client.post(f"/api/activities/{aid}/archive-article-image",
                    files={"file": ("shot.png", io.BytesIO(_real_png(8, 8)), "image/png")})
    assert r.status_code == 400

    async def fake_chat(messages, **kwargs):
        content = messages[0]["content"]
        assert any(p["type"] == "image_url" for p in content)
        return json.dumps({"is_article": True, "title": "截取到的标题", "text": "从截图解析出的正文全文"},
                          ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(f"/api/activities/{aid}/archive-article-image",
                    files={"file": ("shot.png", io.BytesIO(_real_png(8, 8)), "image/png")},
                    data={"url": "https://mp.weixin.qq.com/s/shot", "op_id": "1"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["title"] == "截取到的标题"

    d = client.get(f"/api/activities/{aid}").json()
    assert d["article_title"] == "截取到的标题" and "解析出的正文" in d["article_text"]
    from backend.app import db
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT detail FROM activity_log WHERE activity_id=? AND action='archive'", (aid,)
        ).fetchone()
        assert "截图解析" in row["detail"]


def test_archive_article_image_rejects_non_article(client, monkeypatch):
    """非推文截图拒收：多模态判定 is_article=false → 任务失败，活动字段保持不变。"""
    from backend.app import llm

    aid = client.post("/api/activities", json={"name": "拒收测试活动"}).json()["id"]

    async def fake_chat(messages, **kwargs):
        return json.dumps({"is_article": False, "title": "", "text": ""}, ensure_ascii=False)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(f"/api/activities/{aid}/archive-article-image",
                    files={"file": ("聊天记录.png", io.BytesIO(_real_png(8, 8)), "image/png")},
                    data={"url": "https://mp.weixin.qq.com/s/x", "op_id": "1"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed"
    assert "不是推文页面" in job["error"]
    d = client.get(f"/api/activities/{aid}").json()
    assert d["article_title"] == "" and d["article_text"] == ""


def test_archive_article_image_tiles_long_screenshot(client, monkeypatch):
    """超长截图自动切片（视觉 API 拒收上万像素整图）：首片取标题，后续片转写，
    重叠带按行去重合并。"""
    import struct
    import zlib
    from io import BytesIO

    def _chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))

    # 400x4000 的白底 PNG（高度超过切片阈值 → 应切 3 片）
    w, h = 400, 4000
    scanlines = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    tall_png = (b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + _chunk(b"IDAT", zlib.compress(scanlines))
                + _chunk(b"IEND", b""))

    aid = client.post("/api/activities", json={"name": "长图切片活动"}).json()["id"]
    from backend.app import llm

    calls = {"n": 0}

    async def fake_chat(messages, **kwargs):
        calls["n"] += 1
        content = messages[0]["content"]
        assert any(p["type"] == "image_url" for p in content)
        if calls["n"] == 1:
            return json.dumps(
                {"is_article": True, "title": "标题来自首片", "text": "第一段正文\n第二段正文\n重叠行"},
                ensure_ascii=False,
            )
        if calls["n"] == 2:
            return "重叠行\n第三段正文"
        return "第四段正文\n——END——"

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = client.post(f"/api/activities/{aid}/archive-article-image",
                    files={"file": ("长图.png", BytesIO(tall_png), "image/png")},
                    data={"url": "https://mp.weixin.qq.com/s/tall", "op_id": "1"})
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done"
    assert calls["n"] == 3, "4000px 应切成 3 片逐段识别"
    d = client.get(f"/api/activities/{aid}").json()
    assert d["article_title"] == "标题来自首片"
    body = d["article_text"]
    assert "第一段正文" in body and "第四段正文" in body
    assert body.count("重叠行") == 1, "重叠带应去重"


def test_report_generate_feeds_photos_multimodal(client, monkeypatch):
    """通讯稿生成：现场照片以多模态图片随消息附带（不是文件名清单）。"""
    from backend.app import llm

    act = client.post("/api/activities", json={"name": "带照片的通讯稿", "event_date": "2026-11-11"}).json()
    aid = act["id"]
    client.post(f"/api/activities/{aid}/files",
                files={"file": ("合影.png", io.BytesIO(_real_png(8, 8)), "image/png")},
                data={"category": "photo", "caption": "合影"})

    seen = {}

    async def fake_chat(messages, **kwargs):
        seen["messages"] = messages
        return "生成好的通讯稿正文"

    monkeypatch.setattr(llm, "chat", fake_chat)
    job = _wait_job(client, client.post(f"/api/activities/{aid}/report/generate").json()["job_id"])
    assert job["status"] == "done"

    user_msg = seen["messages"][-1]
    content = user_msg["content"]
    assert isinstance(content, list)
    images = [p for p in content if p.get("type") == "image_url"]
    assert images, "照片应以多模态图片随消息附带"
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    text = next(p["text"] for p in content if p.get("type") == "text")
    assert "合影" not in text or "文件名" not in text  # 提示词要求按画面呼应而非罗列文件


def test_publish_confirm_requires_publish_stage(client, monkeypatch):
    """跳级守卫：未推进到推文发布、或没有通讯稿时不能确认归档（即使文章已归档）。"""
    aid = client.post("/api/activities", json={"name": "跳级测试", "event_date": "2026-12-01"}).json()["id"]
    from backend.app import db
    with db.get_conn() as conn:
        conn.execute("UPDATE activities SET article_url='https://u/x', article_title='标题',"
                     " article_text='正文' WHERE id=?", (aid,))
    # 阶段仍在 dispatch → 400
    r = client.post(f"/api/activities/{aid}/publish-confirm")
    assert r.status_code == 400 and "推文发布" in r.json()["detail"]
    # 推进到 publish 但通讯稿为空 → 400
    with db.get_conn() as conn:
        conn.execute("UPDATE activities SET stage='publish' WHERE id=?", (aid,))
    r = client.post(f"/api/activities/{aid}/publish-confirm")
    assert r.status_code == 400 and "通讯稿" in r.json()["detail"]
    # 补齐后成功
    with db.get_conn() as conn:
        conn.execute("UPDATE activities SET report_text='稿' WHERE id=?", (aid,))
    assert client.post(f"/api/activities/{aid}/publish-confirm").json()["stage"] == "done"
