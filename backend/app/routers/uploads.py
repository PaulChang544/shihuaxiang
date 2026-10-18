"""活动附件（照片/报销票据）上传与访问。文件落盘 storage/uploads/，路径相对项目根。"""

import pathlib
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .. import auth, db, filesec, thumbs

router = APIRouter()
uploads_router = APIRouter()

ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".pdf", ".doc", ".docx"}
MAX_SIZE = 20 * 1024 * 1024
CATEGORIES = ("photo", "receipt")

# 静态附件响应头：禁止 MIME 嗅探；非图片强制下载，避免同源 HTML 执行脚本
ATTACHMENT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "attachment",
}


def _serve_headers(row) -> dict:
    """按附件类别生成响应头：照片内联且可私下缓存；票据等强制下载、禁缓存。"""
    if row["category"] == "photo":
        return {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=2592000",
        }
    return {
        **ATTACHMENT_HEADERS,
        "Cache-Control": "private, no-store",
    }


@router.post("/api/activities/{aid}/files")
async def upload_file(
    aid: int,
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("photo"),
    caption: str = Form(""),
):
    category = category.strip() or "photo"
    if category not in CATEGORIES:
        raise HTTPException(400, "附件类型仅支持 photo / receipt")
    with db.get_conn() as conn:
        if conn.execute("SELECT id FROM activities WHERE id=?", (aid,)).fetchone() is None:
            raise HTTPException(404, "活动不存在")
    ext = pathlib.Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"不支持的文件类型：{ext or '（无后缀）'}")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, "文件超过 20MB 上限")
    if not data:
        raise HTTPException(400, "文件内容为空")
    # 内容级校验：不信任客户端 MIME 与扩展名，按真实字节判定并规范化
    try:
        mime, norm_ext = filesec.sniff_upload(data, file.filename or "", category)
    except ValueError as e:
        raise HTTPException(400, str(e))
    rel_dir = f"storage/uploads/act_{aid}/{category}"
    dest = db.ROOT / rel_dir
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{norm_ext}"
    (dest / name).write_bytes(data)
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO uploads(activity_id, category, filename, stored_path, mime, size, caption)"
            " VALUES(?,?,?,?,?,?,?)",
            (aid, category, file.filename or name, f"{rel_dir}/{name}",
             mime, len(data), caption.strip()),
        )
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (cur.lastrowid,)).fetchone()
        kind = "照片" if category == "photo" else "票据"
        name_note = (caption.strip() or (file.filename or name))
        db.log_action(conn, f"上传了{kind}「{name_note}」",
                      actor_id=auth.current_member(request)["id"], activity_id=aid, action="upload")
    out = dict(row)
    out["thumb_path"] = _finalize_photo(aid, category, f"{rel_dir}/{name}", row["id"])
    return out


def _finalize_photo(aid: int, category: str, stored_path: str, row_id: int) -> str:
    """照片入库后即刻生成缩略图（失败静默，路由会回落原图）。返回 thumb_path 或 ''。"""
    if category != "photo":
        return ""
    tp = thumbs.make_thumb(stored_path)
    if tp:
        with db.get_conn() as conn:
            conn.execute("UPDATE uploads SET thumb_path=? WHERE id=?", (tp, row_id))
    return tp


@router.get("/api/files/{fid}")
def get_file(fid: int):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (fid,)).fetchone()
    if row is None:
        raise HTTPException(404, "文件不存在")
    path = db.ROOT / row["stored_path"]
    if not path.exists():
        raise HTTPException(404, "文件已丢失")
    # 照片内联预览；票据等一律 attachment 强制下载，杜绝同源 HTML 执行
    return FileResponse(path, media_type=row["mime"] or "application/octet-stream",
                        headers=_serve_headers(row))


@router.get("/api/files/{fid}/thumb")
def get_file_thumb(fid: int):
    """缩略图（照片墙专用）：只对照片提供；无缩略图时回落原图。票据等返回 404。"""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (fid,)).fetchone()
    if row is None or row["category"] != "photo":
        raise HTTPException(404, "文件不存在")
    tp = row["thumb_path"]
    path = (db.ROOT / tp) if tp else None
    if not path or not path.exists():
        path = db.ROOT / row["stored_path"]  # 缩略图缺失降级原图，比空格子好
        if not path.exists():
            raise HTTPException(404, "文件已丢失")
    return FileResponse(path, media_type="image/jpeg" if (tp and (db.ROOT / tp).exists()) else row["mime"],
                        headers={"X-Content-Type-Options": "nosniff",
                                 "Cache-Control": "private, max-age=2592000"})


def _remove_file(row) -> None:
    path = db.ROOT / row["stored_path"]
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass  # 磁盘清理失败不阻塞记录删除


@router.delete("/api/files/{fid}")
def delete_file(fid: int, request: Request):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (fid,)).fetchone()
        if row is None:
            raise HTTPException(404, "文件不存在")
        conn.execute("DELETE FROM uploads WHERE id=?", (fid,))
        kind = "照片" if row["category"] == "photo" else "票据"
        db.log_action(conn, f"删除了{kind}「{row['caption'] or row['filename']}」",
                      actor_id=auth.current_member(request)["id"],
                      activity_id=row["activity_id"], action="file_delete")
    _remove_file(row)
    return {"ok": True}


# ---- 分片断点续传上传（大图本地压缩后走这里；意外中断可从已收偏移续传） ----

import datetime as _dt

CHUNK_SESSION_TTL_HOURS = 24


def _session_dir() -> pathlib.Path:
    d = db.ROOT / "storage" / "uploads" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


_UID_RE = __import__("re").compile(r"^[0-9a-f]{32}$")


def _part_path(uid: str) -> pathlib.Path:
    if not _UID_RE.match(uid or ""):
        raise HTTPException(400, "上传会话编号不合法")
    return _session_dir() / f"{uid}.part"


def _purge_stale_sessions(conn) -> None:
    """超过 TTL 的会话连同半成品文件一并清理（惰性触发）。"""
    rows = conn.execute("SELECT id FROM upload_sessions").fetchall()
    cutoff = (_dt.datetime.now() - _dt.timedelta(hours=CHUNK_SESSION_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        row = conn.execute("SELECT created_at FROM upload_sessions WHERE id=?", (r["id"],)).fetchone()
        if row and row["created_at"] < cutoff:
            conn.execute("DELETE FROM upload_sessions WHERE id=?", (r["id"],))
            p = _part_path(r["id"])
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


@uploads_router.post("/api/uploads/init")
def upload_init(data: dict, request: Request):
    """登记一个分片上传会话；同名会话已存在则返回当前已收字节数（续传入口）。"""
    import uuid as _uuid

    aid = int(data.get("activity_id") or 0)
    category = (data.get("category") or "photo").strip()
    if category not in CATEGORIES:
        raise HTTPException(400, "附件类型仅支持 photo / receipt")
    size = int(data.get("size") or 0)
    if size <= 0 or size > MAX_SIZE:
        raise HTTPException(400, "文件大小不合法（上限 20MB）")
    filename = (data.get("filename") or "").strip()
    if not filesec.allowed_ext_for_init(filename, category):
        raise HTTPException(400, "不支持的文件类型（照片仅图片；票据仅图片/PDF/Word）")
    with db.get_conn() as conn:
        if conn.execute("SELECT id FROM activities WHERE id=?", (aid,)).fetchone() is None:
            raise HTTPException(404, "活动不存在")
        _purge_stale_sessions(conn)
        uid = _uuid.uuid4().hex
        conn.execute(
            "INSERT INTO upload_sessions(id, activity_id, category, caption, filename, mime, size, received, op_id)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (uid, aid, category, (data.get("caption") or "").strip(), data.get("filename") or "",
             data.get("mime") or "", size, 0, auth.current_member(request)["id"]),
        )
    _part_path(uid).touch()
    return {"upload_id": uid, "received": 0}


@uploads_router.get("/api/uploads/{uid}")
def upload_status(uid: str):
    with db.get_conn() as conn:
        row = conn.execute("SELECT received, size FROM upload_sessions WHERE id=?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(404, "上传会话不存在（可能已过期）")
    return {"received": row["received"], "size": row["size"]}


@uploads_router.put("/api/uploads/{uid}")
async def upload_chunk(uid: str, request: Request):
    """顺序追加一个分片；request 体为原始字节。"""
    with db.get_conn() as conn:
        row = conn.execute("SELECT size, received, mime FROM upload_sessions WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise HTTPException(404, "上传会话不存在（可能已过期）")
    remaining = row["size"] - row["received"]
    body = bytearray()
    async for piece in request.stream():
        body.extend(piece)
        if len(body) > remaining:
            raise HTTPException(400, "分片超出登记的文件大小")
    body = bytes(body)
    if not body:
        raise HTTPException(400, "分片内容为空")
    part = _part_path(uid)
    with open(part, "ab") as f:
        f.write(body)
    received = row["received"] + len(body)
    with db.get_conn() as conn:
        conn.execute("UPDATE upload_sessions SET received=? WHERE id=?", (received, uid))
    return {"received": received}


@uploads_router.post("/api/uploads/{uid}/finish")
def upload_finish(uid: str):
    """收尾：把半成品转正为正式附件（写库 + 操作记录）。"""
    import uuid as _uuid

    with db.get_conn() as conn:
        sess = conn.execute("SELECT * FROM upload_sessions WHERE id=?", (uid,)).fetchone()
        if sess is None:
            raise HTTPException(404, "上传会话不存在（可能已过期）")
        if sess["received"] != sess["size"]:
            raise HTTPException(400, f"分片未传完（{sess['received']}/{sess['size']}）")
    part = _part_path(uid)
    if not part.exists() or part.stat().st_size != sess["size"]:
        raise HTTPException(400, "分片文件不完整，请重新上传")
    data = part.read_bytes()
    # 内容级校验：客户端声明的 MIME 一律不采信，按真实字节判定；不合法则丢弃半成品
    try:
        mime, norm_ext = filesec.sniff_upload(data, sess["filename"] or "", sess["category"])
    except ValueError as e:
        part.unlink(missing_ok=True)
        with db.get_conn() as conn:
            conn.execute("DELETE FROM upload_sessions WHERE id=?", (uid,))
        raise HTTPException(400, str(e))
    rel_dir = f"storage/uploads/act_{sess['activity_id']}/{sess['category']}"
    dest = db.ROOT / rel_dir
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{_uuid.uuid4().hex}{norm_ext}"
    (dest / name).write_bytes(data)
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO uploads(activity_id, category, filename, stored_path, mime, size, caption)"
            " VALUES(?,?,?,?,?,?,?)",
            (sess["activity_id"], sess["category"], sess["filename"] or name,
             f"{rel_dir}/{name}", mime, sess["size"], sess["caption"]),
        )
        row = conn.execute("SELECT * FROM uploads WHERE id=?", (cur.lastrowid,)).fetchone()
        kind = "照片" if sess["category"] == "photo" else "票据"
        db.log_action(conn, f"上传了{kind}「{sess['caption'] or sess['filename'] or name}」",
                      actor_id=sess["op_id"], activity_id=sess["activity_id"], action="upload")
        conn.execute("DELETE FROM upload_sessions WHERE id=?", (uid,))
    part.unlink(missing_ok=True)
    out = dict(row)
    out["thumb_path"] = _finalize_photo(sess["activity_id"], sess["category"], f"{rel_dir}/{name}", row["id"])
    return out


@uploads_router.delete("/api/uploads/{uid}")
def upload_abort(uid: str):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM upload_sessions WHERE id=?", (uid,))
    p = _part_path(uid)
    if p.exists():
        p.unlink(missing_ok=True)
    return {"ok": True}
