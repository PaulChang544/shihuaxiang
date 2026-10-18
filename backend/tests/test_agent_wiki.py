import pathlib
import pytest
from backend.app import db
from backend.app.agent import wiki


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO materials(name, activity_type, time, form_points, highlights, participation, raw_text, source_url)"
            " VALUES('「漆扇绘新岁」元旦非遗活动','手工 DIY','2026-01-01','漆扇制作、拓印DIY','亲手完成作品','30人参与','原文内容……','https://mp.weixin.qq.com/s/x')"
        )
        conn.execute(
            "INSERT INTO materials(name, activity_type, time, form_points, highlights, participation, raw_text)"
            " VALUES('重阳节敬老志愿活动','志愿服务','2025-10-29','入户打扫、陪伴聊天','青春暖夕阳','志愿者20人','原文内容……')"
        )
    return tmp_path


def test_slugify_cleans_punctuation():
    assert wiki.slugify("「漆扇绘新岁」元旦非遗活动!") == "漆扇绘新岁-元旦非遗活动"
    assert len(wiki.slugify("长" * 80)) <= 40


def test_build_wiki_creates_index_and_pages(seeded, tmp_path, monkeypatch):
    out = tmp_path / "wiki"
    with db.get_conn() as conn:
        result = wiki.build_wiki(conn, out_dir=out)
    assert result["pages"] == 2
    index = (out / "index.md").read_text(encoding="utf-8")
    assert "手工 DIY" in index and "志愿服务" in index
    page = next((out / "pages").glob("*漆扇*"))
    text = page.read_text(encoding="utf-8")
    assert "## 形式要点" in text and "原文内容" in text
    # 幂等：重复执行不产生重复条目
    with db.get_conn() as conn:
        result2 = wiki.build_wiki(conn, out_dir=out)
    assert result2["pages"] == 2


def test_wiki_search_hits_name_and_form_points(seeded):
    hits = wiki.wiki_search("漆扇")
    assert hits and hits[0]["name"].startswith("「漆扇")
    assert wiki.wiki_search("入户")[0]["activity_type"] == "志愿服务"
    assert wiki.wiki_search("漆扇", activity_type="志愿服务") == []


def test_wiki_read_missing_slug_returns_hint(seeded):
    assert "未找到" in wiki.wiki_read("不存在页面")


def test_duplicate_names_get_unique_slugs(tmp_path, monkeypatch):
    """同名活动（素材库真实存在）不应互相覆盖页面，且 wiki_search 返回的 slug 可读。"""
    from backend.app import db
    from backend.app.agent import wiki
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO materials(name, activity_type, raw_text) VALUES('夜学共进，高数互助','学习互助','第一场')")
        conn.execute("INSERT INTO materials(name, activity_type, raw_text) VALUES('夜学共进，高数互助','学习互助','第二场')")
    # 插入提交后再构建/检索（另一连接才能读到数据）
    with db.get_conn() as conn:
        result = wiki.build_wiki(conn, out_dir=tmp_path / "wiki")
    assert result["pages"] == 2  # 两个真实页面文件
    hits = wiki.wiki_search("夜学共进")
    slugs = sorted(h["slug"] for h in hits)
    assert len(set(slugs)) == 2
    contents = "".join((tmp_path / "wiki" / "pages" / f"{s}.md").read_text(encoding="utf-8") for s in slugs)
    assert "第一场" in contents and "第二场" in contents


def test_wiki_read_rejects_path_traversal(seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "WIKI_DIR", tmp_path / "wiki")
    with db.get_conn() as conn:
        wiki.build_wiki(conn, out_dir=tmp_path / "wiki")
    secret = tmp_path / "secret.txt"
    secret.write_text("机密内容", encoding="utf-8")
    # 各种穿越写法一律拒绝，不返回任何文件内容
    backslash = chr(92)
    for bad in ("../secret", "..%2Fsecret", "../../storage/storage.db", "a/b", "a" + backslash + "b", ".."):
        out = wiki.wiki_read(bad)
        assert "机密内容" not in out
        assert "无效的 slug" in out
    # 正常 slug 仍可读取
    slug = wiki.wiki_search("漆扇")[0]["slug"]
    assert "形式要点" in wiki.wiki_read(slug)


def test_material_routes_trigger_rebuild(tmp_path, monkeypatch):
    """素材入库/更新/删除都会触发 wiki 重建钩子（rebuild 已在 conftest 里替为 no-op）。"""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    calls = []
    monkeypatch.setattr(wiki, "rebuild", lambda: calls.append(1))
    client = TestClient(app)
    assert client.post("/api/members/claim", json={"code": db.SUPER_CODE}).status_code == 200

    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO materials(name, source_url, raw_text) VALUES('夜学共进，高数互助','https://u/a','正文')"
        )
        mid = cur.lastrowid

    client.put(f"/api/materials/{mid}", json={"name": "夜学共进，高数互助（改）"})
    assert calls == [1]
    client.delete(f"/api/materials/{mid}")
    assert calls == [1, 1]


def test_publish_confirm_relaunches_rebuild(tmp_path, monkeypatch):
    """活动确认发布（推文自动入素材库）后同样触发 wiki 重建。"""
    from fastapi.testclient import TestClient
    from backend.app.main import app

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    calls = []
    monkeypatch.setattr(wiki, "rebuild", lambda: calls.append(1))
    client = TestClient(app)
    assert client.post("/api/members/claim", json={"code": db.SUPER_CODE}).status_code == 200

    aid = client.post("/api/activities", json={"name": "X", "event_date": "2026-11-01"}).json()["id"]
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE activities SET article_url='https://u/x', article_title='回顾', article_text='文',"
            " stage='publish', report_text='稿' WHERE id=?",
            (aid,),
        )
    assert client.post(f"/api/activities/{aid}/publish-confirm").json()["material_synced"] is True
    assert calls == [1]
