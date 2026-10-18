"""通用异步作业：长周期服务端任务（AI 识别/提取、Word 构建等）统一入表，前端轮询并通知。

状态机：pending → running → done | failed
- result 存 JSON 字符串（完成负载，如文件名/素材 id），error 存给用户看的原因
- 作业独立于请求与页面生命周期；服务重启时由 init_db 把遗留任务标记失败
"""

import json

from . import db

KINDS = ("archive_article", "docx_export", "material_extract", "material_enhance", "report_generate")


def create_job(kind: str, *, ref_id: int | None = None, label: str = "", op_id: int | None = None) -> int:
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(kind, status, ref_id, label, op_id) VALUES(?, 'pending', ?, ?, ?)",
            (kind, ref_id, label, op_id),
        )
        return cur.lastrowid


def update_job(jid: int, *, status: str | None = None, label: str | None = None,
               result: dict | None = None, error: str | None = None) -> None:
    sets, args = [], []
    if status is not None:
        sets.append("status=?")
        args.append(status)
    if label is not None:
        sets.append("label=?")
        args.append(label)
    if result is not None:
        sets.append("result=?")
        args.append(json.dumps(result, ensure_ascii=False))
    if error is not None:
        sets.append("error=?")
        args.append(error)
    if not sets:
        return
    args.append(jid)
    with db.get_conn() as conn:
        conn.execute(
            f"UPDATE jobs SET {', '.join(sets)}, updated_at=datetime('now','localtime') WHERE id=?",
            tuple(args),
        )


def get_job(jid: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["result"] = json.loads(out["result"] or "{}")
    except (TypeError, ValueError):
        out["result"] = {}
    return out


def purge_old_jobs(days: int = 7) -> None:
    """清理一周前的历史作业，防止表膨胀。"""
    with db.get_conn() as conn:
        conn.execute(
            "DELETE FROM jobs WHERE created_at < datetime('now','localtime', ?)",
            (f"-{days} days",),
        )


__all__ = ["KINDS", "create_job", "update_job", "get_job", "purge_old_jobs"]
