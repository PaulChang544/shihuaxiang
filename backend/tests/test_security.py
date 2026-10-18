"""安全行为测试：上传内容嗅探、响应头、SSRF 防护、文档暴露面。"""

import io

import pytest

from backend.tests.test_uploads_api import _real_png


def _activity(client):
    return client.post("/api/activities", json={"name": "安全测试"}).json()["id"]


def test_upload_rejects_disguised_html_photo(client):
    """伪装成 .jpg 的 HTML 必须被内容嗅探拒绝（S2 同源 XSS 的根因）。"""
    aid = _activity(client)
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("x.jpg", io.BytesIO(b"<script>alert(1)</script>"), "image/jpeg")},
        data={"category": "photo"},
    )
    assert r.status_code == 400
    assert "照片" in r.json()["detail"]


def test_upload_ignores_client_mime_and_serves_safely(client):
    """MIME 由服务端嗅探决定；文件响应带 nosniff；票据强制 attachment。"""
    aid = _activity(client)
    # 真 PNG 却声明 text/html：服务端应按真实内容入库为 image/png
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("x.png", io.BytesIO(_real_png()), "text/html")},
        data={"category": "photo"},
    )
    assert r.status_code == 200
    meta = r.json()
    assert meta["mime"] == "image/png"
    got = client.get(f"/api/files/{meta['id']}")
    assert got.status_code == 200
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "attachment" not in (got.headers.get("content-disposition") or "")
    assert got.headers["cache-control"].startswith("private")

    # 票据（PDF）：入库 pdf，下载强制 attachment + no-store
    rp = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("票.pdf", io.BytesIO(b"%PDF-1.4\nfake"), "application/pdf")},
        data={"category": "receipt"},
    )
    assert rp.status_code == 200 and rp.json()["mime"] == "application/pdf"
    rp_get = client.get(f"/api/files/{rp.json()['id']}")
    assert rp_get.headers["content-disposition"] == "attachment"
    assert rp_get.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in rp_get.headers["cache-control"]


def test_chunked_upload_validates_extension_and_content(client):
    """分片上传：init 拒绝非图片扩展名；finish 对伪装内容拒绝并清理会话。"""
    aid = _activity(client)
    bad_init = client.post("/api/uploads/init", json={
        "activity_id": aid, "category": "photo", "filename": "evil.html",
        "mime": "text/html", "size": 20,
    })
    assert bad_init.status_code == 400

    payload = b"<script>alert(1)</script>"
    init = client.post("/api/uploads/init", json={
        "activity_id": aid, "category": "photo", "filename": "x.jpg",
        "mime": "image/jpeg", "size": len(payload),
    })
    assert init.status_code == 200
    uid = init.json()["upload_id"]
    client.put(f"/api/uploads/{uid}", content=payload)
    fin = client.post(f"/api/uploads/{uid}/finish")
    assert fin.status_code == 400
    # 会话与半成品都清掉
    assert client.get(f"/api/uploads/{uid}").status_code == 404

    # 路径穿越/非法 uid 被拒
    assert client.delete("/api/uploads/..%5C..%5Cvictim").status_code in (400, 404, 405)


def test_security_headers_on_responses(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_docs_disabled_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_ssrf_url_validation(client):
    from backend.app import wechat

    for bad in (
        "http://127.0.0.1:8000/?mp.weixin.qq.com",
        "http://mp.weixin.qq.com.evil.com/s/x",
        "https://mp.weixin.qq.com:8080/s/x",
        "file:///etc/passwd?mp.weixin.qq.com",
    ):
        with pytest.raises(wechat.FetchError):
            wechat._validate_url(bad)

    # 合法域名（mock DNS，避免测试环境联网）：正常通过
    import socket as _socket
    import types

    fake_info = [(2, 1, 6, "", ("203.0.113.10", 0))]
    orig = _socket.getaddrinfo
    _socket.getaddrinfo = lambda *a, **k: fake_info
    try:
        wechat._validate_url("https://mp.weixin.qq.com/s/abc")
    finally:
        _socket.getaddrinfo = orig


def test_request_body_limit(client):
    """带 Content-Length 的超大请求在中间件层即被拒绝（413）。"""
    big = b"0" * (26 * 1024 * 1024)
    r = client.post("/api/uploads/nonexistent", content=big)
    assert r.status_code == 413


def test_agent_session_ownership(client, env):
    """Agent 会话只对创建者或管理员可见。"""
    from fastapi.testclient import TestClient

    sid = client.post("/api/agent/sessions").json()["id"]
    client.post("/api/members", json={"name": "站长甲"})  # 首位成员引导为站长
    member = client.post("/api/members", json={"name": "普通成员"}).json()
    code = client.post(f"/api/members/{member['id']}/invites").json()["code"]
    mc = TestClient(env)
    assert mc.post("/api/members/claim", json={"code": code}).status_code == 200

    assert mc.get(f"/api/agent/sessions/{sid}").status_code == 404
    mine = mc.post("/api/agent/sessions").json()["id"]
    assert mc.get(f"/api/agent/sessions/{mine}").status_code == 200
    assert client.get(f"/api/agent/sessions/{mine}").status_code == 200  # 管理员可看


def test_session_sliding_renewal(client):
    """滑动续期：剩余不足 1 小时时，一次普通请求即把会话延长回 7 天并重发 Cookie。"""
    from datetime import datetime, timedelta
    from backend.app import auth, db

    with db.get_conn() as conn:
        sid = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()["id"]
        soon = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE sessions SET expires_at=? WHERE id=?", (soon, sid))

    r = client.get("/api/members")
    assert r.status_code == 200
    assert "set-cookie" in {k.lower() for k in r.headers.keys()}  # Cookie 同步续期

    with db.get_conn() as conn:
        exp = conn.execute("SELECT expires_at FROM sessions WHERE id=?", (sid,)).fetchone()["expires_at"]
    assert exp > (datetime.now() + timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")


def test_claim_rate_limit_is_per_client_ip(env):
    """claim 限流必须按真实客户端 IP 计数：反代头不同不应共享额度。"""
    from fastapi.testclient import TestClient
    from backend.app import db

    a = TestClient(env)
    b = TestClient(env)
    # IP A：连续 5 次失败（无效码）→ 第 6 次被限流
    for _ in range(5):
        assert a.post("/api/members/claim", json={"code": "AAAAAA"},
                      headers={"X-Real-IP": "203.0.113.9"}).status_code == 404
    assert a.post("/api/members/claim", json={"code": "AAAAAA"},
                  headers={"X-Real-IP": "203.0.113.9"}).status_code == 429
    # IP B：不受 A 的额度影响，可正常登录
    r = b.post("/api/members/claim", json={"code": db.SUPER_CODE},
               headers={"X-Real-IP": "203.0.113.10"})
    assert r.status_code == 200 and r.json()["member"]["role"] == "super"
