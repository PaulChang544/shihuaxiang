import io
import struct
import zlib

from backend.app import db

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _real_png(w: int = 8, h: int = 8) -> bytes:
    """PIL 可解码的真 PNG（服务端内容嗅探要求真实图片，假字节会被拒）。"""
    def _chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))

    scanlines = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(scanlines))
            + _chunk(b"IEND", b""))


def _activity(client, name="秋游"):
    return client.post("/api/activities", json={"name": name}).json()["id"]


def test_upload_roundtrip(client):
    aid = _activity(client)
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("出发.jpg", io.BytesIO(_real_png()), "image/jpeg")},
        data={"category": "photo", "caption": "集合照"},
    )
    assert r.status_code == 200
    meta = r.json()
    assert meta["category"] == "photo" and meta["filename"] == "出发.jpg"
    assert meta["stored_path"].startswith(f"storage/uploads/act_{aid}/photo/")
    assert (db.ROOT / meta["stored_path"]).exists()

    # 内联下载（预览）
    got = client.get(f"/api/files/{meta['id']}")
    assert got.status_code == 200
    assert got.content.startswith(b"\x89PNG")

    # 删除：库与盘都清掉
    assert client.delete(f"/api/files/{meta['id']}").json() == {"ok": True}
    assert client.get(f"/api/files/{meta['id']}").status_code == 404
    assert not (db.ROOT / meta["stored_path"]).exists()


def test_upload_rejects_bad_ext(client):
    aid = _activity(client)
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("x.exe", io.BytesIO(PNG), "application/octet-stream")},
        data={"category": "photo"},
    )
    assert r.status_code == 400


def test_upload_rejects_bad_category(client):
    aid = _activity(client)
    r = client.post(
        f"/api/activities/{aid}/files",
        files={"file": ("x.jpg", io.BytesIO(PNG), "image/jpeg")},
        data={"category": "video"},
    )
    assert r.status_code == 400


def test_upload_requires_existing_activity(client):
    r = client.post(
        "/api/activities/999/files",
        files={"file": ("x.jpg", io.BytesIO(PNG), "image/jpeg")},
    )
    assert r.status_code == 404


def test_file_not_found(client):
    assert client.get("/api/files/99999").status_code == 404
    assert client.delete("/api/files/99999").status_code == 404


def test_photo_thumb_and_cache_headers(client):
    """照片上传即生成缩略图；/thumb 返回小图；/api/files 带长缓存头；票据无缩略图。"""
    import struct
    import zlib

    def _chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))

    # 8x8 灰色真 PNG（带逐行 filter byte，PIL 可解码，才会真的产出缩略图）
    scanlines = b"".join(b"\x00" + b"\x80" * 8 for _ in range(8))
    real_png = (b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0))
                + _chunk(b"IDAT", zlib.compress(scanlines))
                + _chunk(b"IEND", b""))
    aid = client.post("/api/activities", json={"name": "缩略图活动"}).json()["id"]
    from io import BytesIO
    r = client.post(f"/api/activities/{aid}/files",
                    files={"file": ("照片.png", BytesIO(real_png), "image/png")},
                    data={"category": "photo", "caption": "首图"})
    assert r.status_code == 200
    row = r.json()
    assert row["thumb_path"], "照片入库应即刻生成缩略图"
    t = client.get(f"/api/files/{row['id']}/thumb")
    assert t.status_code == 200 and t.headers["content-type"].startswith("image/jpeg")
    assert "max-age=2592000" in t.headers["cache-control"]
    f = client.get(f"/api/files/{row['id']}")
    assert f.status_code == 200 and "max-age=2592000" in f.headers["cache-control"]
    # 票据没有缩略图
    rp = client.post(f"/api/activities/{aid}/files",
                     files={"file": ("票据.pdf", BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
                     data={"category": "receipt"})
    assert rp.status_code == 200
    assert client.get(f"/api/files/{rp.json()['id']}/thumb").status_code == 404
