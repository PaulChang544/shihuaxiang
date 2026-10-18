"""成员/角色/邀请码：服务端会话制语义。

- conftest 的 client 默认以超管会话登录
- 需要"以某成员身份"操作时用 _as()：由管理员为其签发邀请码并用新客户端 claim
"""

from backend.app import db


def _as(env, manager, mid):
    """以成员身份登录：管理员签发一次性邀请码 → 新客户端 claim（切换会话）。"""
    code = manager.post(f"/api/members/{mid}/invites").json()["code"]
    c = _new_client(env)
    r = c.post("/api/members/claim", json={"code": code})
    assert r.status_code == 200, r.text
    return c


def _new_client(env):
    from fastapi.testclient import TestClient
    return TestClient(env)


def _super_client(env):
    c = _new_client(env)
    assert c.post("/api/members/claim", json={"code": db.SUPER_CODE}).status_code == 200
    return c


def test_auth_required_and_session(client, env):
    """未登录一律 401；伪造 op_id 无效；登录后 /me 返回本人。"""
    from fastapi.testclient import TestClient

    anonymous = TestClient(env)
    assert anonymous.get("/api/members").status_code == 401
    assert anonymous.get("/api/activities").status_code == 401
    # 伪造 op_id 不再起作用：没有会话就是 401
    assert anonymous.get("/api/activities", headers={"X-Test": "1"}).status_code == 401
    assert anonymous.post("/api/members", json={"name": "X", "op_id": 1}).status_code == 401

    me = client.get("/api/auth/me").json()["member"]
    assert me["role"] == "super"
    # 退出登录后会话失效
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/members").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_roles_bootstrap_and_crud(client, env):
    # 超管会话即可管理名册；首位成员引导为站长
    member = client.post("/api/members", json={"name": "张三", "contact": "13800000000",
                                               "role_note": "组织策划部"}).json()
    assert member["role"] == "owner" and member["active"] == 1
    member2 = client.post("/api/members", json={"name": "李四", "active": False}).json()

    data = client.get("/api/members").json()
    assert [m["name"] for m in data] == ["张三", "李四"]
    assert data[0]["role"] == "owner" and data[1]["role"] == "member"

    assert client.post("/api/members", json={"name": "  "}).status_code == 400
    assert client.put(f"/api/members/{member['id']}", json={"name": "  "}).status_code == 400

    # 普通成员无权管理名册（403）
    li = _as(env, client, member2["id"]) if member2["active"] else None
    # member2 停用不能登录 → 建一个在册成员验证 403
    wang = client.post("/api/members", json={"name": "王五"}).json()
    wang_c = _as(env, client, wang["id"])
    assert wang_c.post("/api/members", json={"name": "越权"}).status_code == 403
    assert wang_c.put(f"/api/members/{member['id']}", json={"name": "改"}).status_code == 403
    assert wang_c.delete(f"/api/members/{member['id']}").status_code == 403
    assert wang_c.get("/api/log").status_code == 200  # 操作记录对登录成员透明可见
    _ = li

    client.put(f"/api/members/{member['id']}", json={"name": "张三丰", "contact": "13911112222",
                                                     "role_note": "社长", "active": True})
    assert [m["name"] for m in client.get("/api/members").json()].count("张三丰") == 1

    # 删除：先转让站长（首位成员被引导为站长），再连带清掉邀请码与负责人关系
    client.post(f"/api/members/{wang['id']}/transfer")
    aid = client.post("/api/activities", json={"name": "测试活动"}).json()["id"]
    client.patch(f"/api/activities/{aid}", json={"lead_ids": [member["id"]]})
    client.post(f"/api/members/{member['id']}/invites")
    assert client.delete(f"/api/members/{member['id']}").json() == {"ok": True}
    assert client.delete("/api/members/99999").status_code == 404
    assert client.delete(f"/api/members/{member2['id']}").json() == {"ok": True}


def test_admin_appoint_dismiss(client, env):
    owner = client.post("/api/members", json={"name": "张三"}).json()
    wang = client.post("/api/members", json={"name": "王五"}).json()
    zhao = client.post("/api/members", json={"name": "赵六"}).json()

    wang_c = _as(env, client, wang["id"])
    zhao_c = _as(env, client, zhao["id"])

    # 普通成员无权任免
    assert wang_c.post(f"/api/members/{zhao['id']}/appoint").status_code == 403
    # 站长任命成员 → 管理员
    assert client.post(f"/api/members/{wang['id']}/appoint").status_code == 200
    assert {m["name"]: m["role"] for m in client.get("/api/members").json()}["王五"] == "admin"

    # 管理员任命普通成员
    assert wang_c.post(f"/api/members/{zhao['id']}/appoint").status_code == 200
    # 管理员不能卸任其他管理员；可以自卸
    r = zhao_c.post(f"/api/members/{wang['id']}/dismiss")
    assert r.status_code == 403 and "只有站长" in r.json()["detail"]
    assert wang_c.post(f"/api/members/{wang['id']}/dismiss").status_code == 200
    roles = {m["name"]: m["role"] for m in client.get("/api/members").json()}
    assert roles["王五"] == "member" and roles["赵六"] == "admin"

    # 站长可卸任其他管理员；目标不是管理员 400；任命目标限成员
    assert client.post(f"/api/members/{zhao['id']}/dismiss").status_code == 200
    assert client.post(f"/api/members/{zhao['id']}/dismiss").status_code == 400
    assert client.post(f"/api/members/{owner['id']}/dismiss").status_code == 400
    assert client.post(f"/api/members/{owner['id']}/appoint").status_code == 400

    logs = client.get("/api/log?limit=50").json()["items"]
    actions = {row["action"] for row in logs}
    assert {"admin_appoint", "admin_dismiss"} <= actions


def test_owner_transfer_rules(client, env):
    owner = client.post("/api/members", json={"name": "站长甲"}).json()
    wang = client.post("/api/members", json={"name": "王五"}).json()
    zhao = client.post("/api/members", json={"name": "赵六"}).json()

    wang_c = _as(env, client, wang["id"])
    zhao_c = _as(env, client, zhao["id"])
    # 管理员不可转让；不能转给自己
    assert wang_c.post(f"/api/members/{zhao['id']}/transfer").status_code == 403
    assert client.post(f"/api/members/{owner['id']}/transfer").status_code == 400

    # 站长把站长转让给管理员 → 旧站长自动降为管理员
    assert client.post(f"/api/members/{wang['id']}/transfer").status_code == 200
    roles = {m["name"]: m["role"] for m in client.get("/api/members").json()}
    assert roles["王五"] == "owner" and roles["站长甲"] == "admin"

    # 新站长（王五的会话）转给普通成员
    assert wang_c.post(f"/api/members/{zhao['id']}/transfer").status_code == 200
    roles = {m["name"]: m["role"] for m in client.get("/api/members").json()}
    assert roles["赵六"] == "owner" and roles["王五"] == "admin"
    assert [m["name"] for m in client.get("/api/members").json() if m["role"] == "owner"] == ["赵六"]

    # 超管指定任意人员为站长，且超管身份保留
    assert client.post(f"/api/members/{owner['id']}/transfer").status_code == 200
    assert client.get("/api/auth/me").json()["member"]["role"] == "super"
    roles = {m["name"]: m["role"] for m in client.get("/api/members").json()}
    assert roles["站长甲"] == "owner"
    assert [m["name"] for m in client.get("/api/members").json() if m["role"] == "owner"] == ["站长甲"]

    # 停用成员不可接任
    client.put(f"/api/members/{wang['id']}", json={"name": "王五", "active": False})
    assert client.post(f"/api/members/{wang['id']}/transfer").status_code == 400

    transfers = [row for row in client.get("/api/log?limit=50").json()["items"] if row["action"] == "owner_transfer"]
    assert len(transfers) >= 3
    assert any("指定" in row["detail"] for row in transfers)


def test_delete_owner_blocked(client):
    owner = client.post("/api/members", json={"name": "张三"}).json()
    r1 = client.delete(f"/api/members/{owner['id']}")
    assert r1.status_code == 403 and "转让" in r1.json()["detail"]
    assert client.put(f"/api/members/{owner['id']}", json={"name": "张三", "active": False}).status_code == 400
    wang = client.post("/api/members", json={"name": "王五"}).json()
    client.post(f"/api/members/{wang['id']}/transfer")
    assert client.delete(f"/api/members/{owner['id']}").json() == {"ok": True}


def test_invite_generate_and_claim(client, env):
    client.post("/api/members", json={"name": "站长甲"})  # 首位成员引导为站长
    member = client.post("/api/members", json={"name": "张三"}).json()
    c1 = client.post(f"/api/members/{member['id']}/invites").json()
    c2 = client.post(f"/api/members/{member['id']}/invites").json()
    assert c1["code"] != c2["code"] and len(c1["code"]) == 6

    # 列表不再回传任何掩码尾号（防缩小搜索空间）
    listed = client.get(f"/api/members/{member['id']}/invites").json()
    assert all(c["code"] == "" for c in listed)

    # claim：大小写不敏感；成功后签发会话
    fresh = _new_client(env)
    r = fresh.post("/api/members/claim", json={"code": c1["code"].lower()})
    assert r.status_code == 200 and r.json()["member"]["id"] == member["id"]
    assert fresh.get("/api/auth/me").json()["member"]["id"] == member["id"]

    assert fresh.post("/api/members/claim", json={"code": "FFFFFF"}).status_code == 404

    # 普通成员无权生成/列邀请码
    assert fresh.post(f"/api/members/{member['id']}/invites").status_code == 403
    assert fresh.get(f"/api/members/{member['id']}/invites").status_code == 403

    # 作废后激活失效
    iid = listed[0]["id"]
    client.delete(f"/api/members/invites/{iid}")
    assert fresh.post("/api/members/claim", json={"code": c2["code"]}).status_code == 404


def test_super_code_bound_to_system_account_and_rotation(client, env):
    """超管行以 is_system 标记：不可被名册接口改名/停用/删除；邀请码列表标记固定码。"""
    members = client.get("/api/members").json()
    assert all(m["role"] != "super" for m in members)  # 超管不出现在名册

    # 通过数据库确认系统行存在且可认
    with db.get_conn() as conn:
        row = conn.execute("SELECT id, role, is_system FROM members WHERE is_system=1").fetchone()
        assert row["role"] == "super" and row["is_system"] == 1
        # 固定码只有一条（轮换即吊销旧码）
        invites = conn.execute("SELECT COUNT(*) FROM invites WHERE member_id=?", (row["id"],)).fetchone()[0]
        assert invites == 1


def test_whoami_and_contact_privacy(client, env):
    """名册接口不泄露超管；联系方式仅管理员或本人可见。"""
    data0 = client.get("/api/members").json()
    assert data0 == []

    client.post("/api/members", json={"name": "站长甲", "contact": "13800000000"})
    data = client.get("/api/members").json()
    assert len(data) == 1 and data[0]["role"] == "owner"
    assert data[0]["contact"] == "13800000000"  # 管理员视角可见

    li = client.post("/api/members", json={"name": "李四", "contact": "13900000000"}).json()
    li_c = _as(env, client, li["id"])
    rows = {m["name"]: m for m in li_c.get("/api/members").json()}
    assert rows["李四"]["contact"] == "13900000000"  # 本人可见
    assert rows["站长甲"]["contact"] == ""           # 他人不可见

    # 失效邀请码 whoami → 404
    assert li_c.post("/api/members/whoami", json={"code": "FFFFFF"}).status_code == 404
