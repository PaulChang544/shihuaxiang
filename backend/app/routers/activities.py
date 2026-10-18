"""活动全生命周期：建活动 → 派发确认 → 六阶段推进 → 发布归档。"""

import asyncio
import base64
import datetime
import pathlib
import shutil
import urllib.parse
import re
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import auth, db, docx_report, jobs, llm, prompts, wechat
from ..agent import wiki
from ..schemas import ArticleInfo
from .materials import enhance_material

router = APIRouter(prefix="/api/activities")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

STAGE_LABELS = {
    "dispatch": "派发确认", "planning": "活动策划", "procurement": "采购报销",
    "execution": "举办中", "coverage": "影像稿件", "publish": "推文发布", "done": "已归档",
}

# 字段 → 中文标签（操作记录用）
FIELD_LABELS = {
    "name": "名称", "activity_type": "类型", "dispatch_note": "派发备注",
    "plan_note": "策划要点", "event_date": "举办日期", "location": "地点",
    "participants": "参与人数", "summary_note": "举办小结", "report_text": "通讯稿",
    "xiumi_url": "秀米链接", "article_url": "文章链接", "plan_ddl": "策划 DDL",
    "recruitment_ddl": "招募推文 DDL", "procurement_ddl": "采购 DDL",
    "recruitment_url": "招募推文链接",
}

CHANGE_LIMIT = 40  # 记录里单个字段值的预览长度


def _preview(v) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= CHANGE_LIMIT else s[:CHANGE_LIMIT] + "…"


def _log(conn, detail: str, *, op_id=None, aid=None, action="", created_at: str | None = None) -> None:
    db.log_action(conn, detail, actor_id=op_id, activity_id=aid, action=action)
    if created_at:
        # 补录用：把刚插入的最新行改成历史时间点
        conn.execute("UPDATE activity_log SET created_at=? WHERE id=last_insert_rowid()", (created_at,))


def _lead_names(conn, aid: int) -> str:
    return conn.execute(
        "SELECT IFNULL((SELECT GROUP_CONCAT(mm.name, '、') FROM activity_leads al"
        " JOIN members mm ON mm.id=al.member_id WHERE al.activity_id=a.id), '')"
        " FROM activities a WHERE a.id=?",
        (aid,),
    ).fetchone()[0]

STAGES = ("dispatch", "planning", "procurement", "execution", "coverage", "publish", "done")

_LIST_SQL_BASE = (
    "SELECT a.*, m.name AS idea_name, m.highlight AS idea_highlight,"
    " (SELECT GROUP_CONCAT(mm.name, '、') FROM activity_leads al"
    "  JOIN members mm ON mm.id=al.member_id WHERE al.activity_id=a.id) AS lead_name,"
    " (SELECT GROUP_CONCAT(al.member_id) FROM activity_leads al WHERE al.activity_id=a.id) AS lead_ids,"
    " (SELECT COALESCE(SUM(amount),0) FROM expenses WHERE activity_id=a.id) AS expense_total,"
    " (SELECT COALESCE(SUM(amount),0) FROM expenses WHERE activity_id=a.id AND reimbursed=1) AS expense_reimbursed,"
    " (SELECT COUNT(*) FROM uploads WHERE activity_id=a.id AND category='photo') AS photo_count"
    " FROM activities a"
    " LEFT JOIN ideas m ON m.id=a.idea_id"
)
LIST_SQL = _LIST_SQL_BASE


def _parse_ids(v) -> list[int]:
    return [int(x) for x in str(v or "").split(",") if x]


def _describe(r) -> dict:
    out = dict(r)
    out["lead_ids"] = _parse_ids(out.get("lead_ids"))
    return out


def _activity_or_404(conn, aid: int):
    row = conn.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if row is None:
        raise HTTPException(404, "活动不存在")
    return row


def _detail(conn, aid: int) -> dict:
    row = conn.execute(
        _LIST_SQL_BASE + " WHERE a.id=?", (aid,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "活动不存在")
    expenses = conn.execute(
        "SELECT * FROM expenses WHERE activity_id=? ORDER BY id DESC", (aid,)
    ).fetchall()
    uploads = conn.execute(
        "SELECT * FROM uploads WHERE activity_id=? ORDER BY id DESC", (aid,)
    ).fetchall()
    out = dict(row)
    out["lead_ids"] = _parse_ids(out.get("lead_ids"))
    out["expenses"] = [dict(e) for e in expenses]
    out["uploads"] = [dict(u) for u in uploads]
    out["ddl_items"] = _ddl_items(out)
    return out


DDL_LABELS = {"plan": "活动策划", "procurement": "采购报销", "recruitment": "发布招募推文"}


def _ddl_items(a: dict) -> list[dict]:
    """详情返回的三类截止项（与主页 ddl-summary 同源）。"""
    today = datetime.date.today()
    items = []
    for kind, col in (("plan", "plan_ddl"), ("recruitment", "recruitment_ddl"), ("procurement", "procurement_ddl")):
        ddl = a.get(col) or ""
        if not ddl or a.get("stage") == "done":
            continue
        try:
            left = (datetime.date.fromisoformat(ddl) - today).days
        except ValueError:
            continue
        items.append({
            "id": f"{a['id']}-{kind}",
            "activity_id": a["id"],
            "name": a.get("name", ""),
            "kind": kind,
            "label": DDL_LABELS[kind],
            "date": ddl,
            "days_left": left,
        })
    return items


class ActivityIn(BaseModel):
    name: str = ""
    activity_type: str = ""
    idea_id: int | None = None
    event_date: str = ""
    plan_ddl: str = ""
    recruitment_ddl: str = ""
    procurement_ddl: str = ""


def _derive_ddls(event_date: str, plan_ddl: str, recruitment_ddl: str, procurement_ddl: str) -> dict:
    """活动日期确定后自动推导默认 DDL（留空才补默认，显式填写以用户为准）：
    招募/策划 DDL = 举办日前 7 天；采购 DDL = 举办日前 3 天。"""
    out = {}
    try:
        event = datetime.date.fromisoformat(event_date)
    except ValueError:
        return out
    if not plan_ddl:
        out["plan_ddl"] = (event - datetime.timedelta(days=7)).isoformat()
    if not recruitment_ddl:
        out["recruitment_ddl"] = (event - datetime.timedelta(days=7)).isoformat()
    if not procurement_ddl:
        out["procurement_ddl"] = (event - datetime.timedelta(days=3)).isoformat()
    return out


@router.post("")
def create_activity(data: ActivityIn, request: Request):
    name = data.name.strip()
    atype = data.activity_type.strip()
    event_date = data.event_date.strip()
    with db.get_conn() as conn:
        if data.idea_id:
            idea = conn.execute("SELECT * FROM ideas WHERE id=?", (data.idea_id,)).fetchone()
            if idea is None:
                raise HTTPException(404, "点子不存在")
            name = name or idea["name"]
            atype = atype or idea["activity_type"]
        if not name:
            raise HTTPException(400, "活动名称不能为空")
        if event_date:
            try:
                datetime.date.fromisoformat(event_date)
            except ValueError:
                raise HTTPException(400, "活动日期格式应为 YYYY-MM-DD")
        ddls = _derive_ddls(event_date, data.plan_ddl.strip(), data.recruitment_ddl.strip(), data.procurement_ddl.strip())
        cur = conn.execute(
            "INSERT INTO activities(name, activity_type, idea_id, event_date, plan_ddl, recruitment_ddl, procurement_ddl)"
            " VALUES(?,?,?,?,?,?,?)",
            (name, atype, data.idea_id, event_date, data.plan_ddl.strip() or ddls.get("plan_ddl", ""),
             data.recruitment_ddl.strip() or ddls.get("recruitment_ddl", ""),
             data.procurement_ddl.strip() or ddls.get("procurement_ddl", "")),
        )
        aid = cur.lastrowid
        _log(conn, f"创建了活动「{name}」", op_id=auth.current_member(request)["id"], aid=aid, action="create")
    with db.get_conn() as conn:
        return _detail(conn, aid)


class ActivityPatch(BaseModel):
    name: str | None = None
    activity_type: str | None = None
    lead_ids: list[int] | None = None
    dispatch_note: str | None = None
    plan_note: str | None = None
    stage: str | None = None
    event_date: str | None = None
    location: str | None = None
    participants: str | None = None
    summary_note: str | None = None
    report_text: str | None = None
    xiumi_url: str | None = None
    article_url: str | None = None
    plan_ddl: str | None = None
    recruitment_ddl: str | None = None
    procurement_ddl: str | None = None
    recruitment_url: str | None = None
    # 操作者身份统一取自服务端会话（op_id 已废弃）


ASSIGNABLE = (
    "name", "activity_type", "dispatch_note", "plan_note", "stage",
    "event_date", "location", "participants", "summary_note", "report_text",
    "xiumi_url", "article_url",
    "plan_ddl", "recruitment_ddl", "procurement_ddl", "recruitment_url",
)


def _valid_date_or_400(key: str, value: str) -> None:
    if key.endswith("_ddl") or key == "event_date":
        try:
            datetime.date.fromisoformat(value)
        except ValueError as e:
            raise HTTPException(400, f"{key} 格式应为 YYYY-MM-DD") from e


@router.get("")
def list_activities(stage: str | None = None, lead_id: int | None = None):
    sql = LIST_SQL
    args: tuple = ()
    conds = []
    if stage:
        conds.append("a.stage=?")
        args += (stage,)
    if lead_id is not None:
        conds.append("EXISTS (SELECT 1 FROM activity_leads al WHERE al.activity_id=a.id AND al.member_id=?)")
        args += (lead_id,)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY a.id DESC"
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_describe(r) for r in rows]


# 主页 DDL 待办：全部活动的三类截止项（跳过已完成，按剩余天数升序）
@router.get("/ddl-summary")
def ddl_summary():
    today = datetime.date.today()
    with db.get_conn() as conn:
        rows = conn.execute(_LIST_SQL_BASE).fetchall()
    out = []
    for row in rows:
        a = dict(row)
        if a.get("stage") == "done":
            continue
        for kind, col in (("plan", "plan_ddl"), ("recruitment", "recruitment_ddl"), ("procurement", "procurement_ddl")):
            ddl = a.get(col) or ""
            if not ddl:
                continue
            try:
                left = (datetime.date.fromisoformat(ddl) - today).days
            except ValueError:
                continue
            out.append({
                "id": f"{a['id']}-{kind}",
                "activity_id": a["id"],
                "name": a.get("name", ""),
                "kind": kind,
                "label": DDL_LABELS[kind],
                "date": ddl,
                "days_left": left,
            })
    out.sort(key=lambda x: x["days_left"])
    return out


@router.get("/{aid}")
def get_activity(aid: int):
    with db.get_conn() as conn:
        return _detail(conn, aid)


@router.patch("/{aid}")
def update_activity(aid: int, data: ActivityPatch, request: Request):
    sets: list[str] = []
    args: list = []
    fields = data.model_fields_set
    for key in set(ASSIGNABLE) & fields:
        value = getattr(data, key)
        if key == "stage":
            if value not in STAGES:
                raise HTTPException(400, f"未知阶段：{value}")
        elif key in ("name", "activity_type") and not (value or "").strip():
            raise HTTPException(400, f"{key} 不能为空")
        elif key == "event_date" or key.endswith("_ddl"):
            _valid_date_or_400(key, value or "")
        sets.append(f"{key}=?")
        args.append("" if value is None else str(value) if isinstance(value, int) else value)
    # 多负责人：lead_ids 与普通字段一样走同一事务（空列表 = 清空并回到待派发）
    if "lead_ids" in fields:
        ids = data.lead_ids or []
        with db.get_conn() as conn:
            for lid in ids:
                if conn.execute("SELECT id FROM members WHERE id=?", (lid,)).fetchone() is None:
                    raise HTTPException(404, f"成员不存在：{lid}")
        sets.append("lead_id=?")
        args.append(ids[0] if ids else None)
        sets.append("dispatch_status=?")
        args.append("assigned" if ids else "draft")
    if not sets:
        raise HTTPException(400, "没有可更新的字段")
    sets.append("updated_at=datetime('now','localtime')")
    with db.get_conn() as conn:
        _activity_or_404(conn, aid)
        before = conn.execute(
            "SELECT name, activity_type, dispatch_note, plan_note, location, participants,"
            " summary_note, report_text, xiumi_url, article_url, recruitment_url,"
            " event_date, plan_ddl, recruitment_ddl, procurement_ddl, stage FROM activities WHERE id=?",
            (aid,),
        ).fetchone()
        # 阶段推进门禁：仅对「前进」生效（回退纠错不受限）；
        # 采购报销、举办阶段允许跳过；弹窗确认由前端负责
        if "stage" in fields:
            order = {s: i for i, s in enumerate(STAGES)}
            old_idx, new_idx = order[before["stage"]], order[data.stage]
            if new_idx > old_idx:
                PLAN_TEXT = before["plan_note"] or ""
                if "plan_note" in fields:
                    PLAN_TEXT = (data.plan_note or "").strip() or PLAN_TEXT
                REPORT_TEXT = before["report_text"] or ""
                if "report_text" in fields:
                    REPORT_TEXT = (data.report_text or "").strip() or REPORT_TEXT
                if new_idx >= order["planning"] and old_idx < order["planning"]:
                    # 同一请求里指派了负责人（lead_ids 非空）即视为满足；
                    # 否则要求活动已有负责人集合
                    has_lead = "lead_ids" in fields and (data.lead_ids or []) != []
                    if not has_lead and not conn.execute(
                        "SELECT 1 FROM activity_leads WHERE activity_id=?", (aid,)
                    ).fetchone():
                        raise HTTPException(400, "进入「策划」前需先指派负责人")
                if new_idx >= order["procurement"] and old_idx < order["procurement"] and not PLAN_TEXT.strip():
                    raise HTTPException(400, "进入「采购报销」前需先填写方案要点")
                if new_idx >= order["publish"] and old_idx < order["publish"] and not REPORT_TEXT.strip():
                    raise HTTPException(400, "进入「推文发布」前需先撰写通讯稿")
                if data.stage == "done" and old_idx < order["done"] and not (before["article_url"] or "").strip():
                    if "article_url" not in fields:
                        raise HTTPException(400, "归档完成前需先填写已发布的微信文章链接")
        conn.execute(f"UPDATE activities SET {', '.join(sets)} WHERE id=?", (*args, aid))
        if "lead_ids" in fields:
            # 负责人集合整体替换（须位于同一事务内，失败可回滚）
            conn.execute("DELETE FROM activity_leads WHERE activity_id=?", (aid,))
            for lid in (data.lead_ids or []):
                conn.execute(
                    "INSERT OR IGNORE INTO activity_leads(activity_id, member_id) VALUES(?,?)", (aid, lid)
                )
        if "report_text" in fields:
            conn.execute(
                "UPDATE activities SET report_updated_at=datetime('now','localtime') WHERE id=?", (aid,)
            )
        # 日期变化时重算跟随日期的默认 DDL：
        # 本次请求显式设置过（含清空）的以用户为准；仍等于旧默认值的视为自动值，随之更新；
        # 与旧默认不符的是用户定制，保留不动。
        if "event_date" in fields:
            def _cand(v):
                if not v:
                    return None
                try:
                    return datetime.date.fromisoformat(v)
                except ValueError:
                    return None
            old_ev, new_ev = _cand(before["event_date"]), _cand(data.event_date)
            for col, delta in (("plan_ddl", 7), ("recruitment_ddl", 7), ("procurement_ddl", 3)):
                if col in fields:
                    continue
                cur = before[col]
                if cur and not (old_ev and cur == (old_ev - datetime.timedelta(days=delta)).isoformat()):
                    continue
                if new_ev:
                    conn.execute(
                        f"UPDATE activities SET {col}=? WHERE id=?",
                        ((new_ev - datetime.timedelta(days=delta)).isoformat(), aid),
                    )
        # 操作记录：全字段差异（每个被改动的字段都入账，含日期/地点/链接等）
        changed: list[str] = []
        for key in set(ASSIGNABLE) & fields - {"stage"}:
            before_txt = "" if before[key] is None else str(before[key])
            after_val = getattr(data, key)
            after_txt = "" if after_val is None else str(after_val)
            if before_txt != after_txt:
                label = FIELD_LABELS.get(key, key)
                changed.append(f"{label} {_preview(after_txt)}" if after_txt else f"{label}（清空）")
        if "lead_ids" in fields:
            names = _lead_names(conn, aid)
            changed.append(f"负责人 {names}" if names else "负责人（清空）")
        logs: list[tuple[str, str]] = []
        if "stage" in fields and data.stage and data.stage != before["stage"]:
            logs.append((data.stage, f"将「{(conn.execute('SELECT name FROM activities WHERE id=?', (aid,)).fetchone() or {'name': ''})['name']}」推进到「{STAGE_LABELS[data.stage]}」"))
        if changed:
            name_now = (conn.execute('SELECT name FROM activities WHERE id=?', (aid,)).fetchone() or {"name": ""})["name"]
            logs.append(("edit", f"更新了「{name_now}」：{'；'.join(changed)}"))
        for action_key, detail in logs:
            _log(conn, detail, op_id=auth.current_member(request)["id"], aid=aid, action=action_key)
        return _detail(conn, aid)


@router.post("/{aid}/confirm")
def confirm_dispatch(aid: int, request: Request):
    """负责人确认接单：以服务端会话成员核对是否为该项目负责人。"""
    actor = auth.current_member(request)
    with db.get_conn() as conn:
        row = _activity_or_404(conn, aid)
        if row["dispatch_status"] == "confirmed":
            return {"ok": True, "confirmed_at": row["confirmed_at"]}
        if not conn.execute(
            "SELECT 1 FROM activity_leads WHERE activity_id=? AND member_id=?", (aid, actor["id"])
        ).fetchone():
            raise HTTPException(403, "仅本项目负责人可确认接单")
        conn.execute(
            "UPDATE activities SET dispatch_status='confirmed', confirmed_at=datetime('now','localtime'),"
            " stage='planning', updated_at=datetime('now','localtime') WHERE id=?",
            (aid,),
        )
        conf_name = conn.execute("SELECT name FROM members WHERE id=?", (actor["id"],)).fetchone()
        actor = actor["id"]
        _log(conn, f"「{(conn.execute('SELECT name FROM activities WHERE id=?', (aid,)).fetchone() or {'name': ''})['name']}」负责人 {conf_name['name'] if conf_name else '?'} 确认了接单",
             op_id=actor, aid=aid, action="confirm")
    with db.get_conn() as conn:
        return _detail(conn, aid)


ARTICLE_IMAGE_MAX = 20 * 1024 * 1024
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


TILE_HEIGHT = 1800          # 单次视觉调用的切片高度（视觉 API 拒绝上万像素高的整图）
TILE_OVERLAP = 140          # 切片重叠带，避免把一行文字切在边界上
MAX_TILES = 15              # 约 2.7 万像素的截图上限


def _article_tiles(src: pathlib.Path):
    """把长截图切成纵向切片（data URL 列表）；短图原样一片。读不出图返回 []。"""
    from PIL import Image, ImageOps
    import io as _io
    try:
        img = Image.open(src)
        if img.width * img.height > 50_000_000:
            return []  # 解压炸弹防护
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return []
    w, h = img.size
    if h <= TILE_HEIGHT:
        buf = _io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return ["data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()]

    step = TILE_HEIGHT - TILE_OVERLAP
    boxes = [(0, y, w, min(y + TILE_HEIGHT, h)) for y in range(0, h, step) if y < h]
    if len(boxes) > MAX_TILES:
        return []
    urls = []
    for box in boxes:
        buf = _io.BytesIO()
        img.crop(box).save(buf, "JPEG", quality=85)
        urls.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
    return urls


EMPTY_MARKERS = {"（空内容）", "(空内容)", "（无内容）", "(无内容)", "（本片无内容）",
                 "（无文字）", "(无文字)", "（空）", "(空)", "空内容", "无内容", "无文字"}


def _merge_tile_text(acc: list[str], chunk: str) -> None:
    """把切片转写并入累计文本：重叠带上的重复行按「尾部==新片头部」最长匹配剔除；
    模型对空片输出的「（空内容）」类占位符直接丢弃。"""
    new_lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    new_lines = [ln for ln in new_lines if ln not in EMPTY_MARKERS]
    if acc and new_lines:
        norm_old = [ln.replace(" ", "") for ln in acc]
        norm_new = [ln.replace(" ", "") for ln in new_lines]
        k = 0
        for kk in range(min(8, len(norm_old), len(norm_new)), 0, -1):
            if norm_old[-kk:] == norm_new[:kk]:
                k = kk
                break
        new_lines = new_lines[k:]
    acc.extend(new_lines)




async def _run_archive_job(job_id: int, aid: int, src: pathlib.Path, url: str, op_id: int | None) -> None:
    """后台识别推文长截图（独立于请求/页面生命周期）：切片转写 → 非推文拒收 → 落库。

    AI 调用通常 20-60 秒，故整体异步；状态写 jobs 供前端跨页面轮询。"""
    jobs.update_job(job_id, status="running")
    try:
        tiles = _article_tiles(src)
        if not tiles:
            raise ValueError("截图无法读取或过长（超过约 2.7 万像素），请分段截图后分别归档")

        title = ""
        lines: list[str] = []
        for i, tile_url in enumerate(tiles):
            msgs = prompts.build_article_tile_messages(tile_url, first=(i == 0))
            if i == 0:
                raw = await llm.chat(msgs, json_mode=True)
                try:
                    info = llm.parse_model(raw, ArticleInfo)
                except llm.AIError:
                    raw = await llm.chat(msgs + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": "你刚才的输出不是合法 JSON，请重新只输出一个 JSON 对象。"},
                    ], json_mode=True)
                    info = llm.parse_model(raw, ArticleInfo)
                if not info.is_article:
                    raise ValueError("这不是推文页面的截图：未识别到推文标题与正文排版。请上传微信公众号推文页面的长截图")
                title = info.title
                _merge_tile_text(lines, info.text)
            else:
                try:
                    chunk = await llm.chat(msgs)
                except llm.AIError:
                    chunk = await llm.chat(msgs)  # 单段失败重试一次
                _merge_tile_text(lines, chunk)

        body = "\n".join(lines).strip()
        if not body:
            raise ValueError("截图中未识别到正文文字。请上传推文页面的完整长截图（不是封面或局部图）")

        with db.get_conn() as conn:
            conn.execute(
                "UPDATE activities SET article_url=?, article_title=?, article_text=?,"
                " updated_at=datetime('now','localtime') WHERE id=?",
                (url, title or "未命名推文", body, aid),
            )
            _log(conn, f"归档了推文《{title or '未命名推文'}》（截图解析 {len(tiles)} 段）",
                 op_id=op_id, aid=aid, action="archive")
        jobs.update_job(job_id, status="done", label=title or "未命名推文", result={"title": title or "未命名推文"})
    except ValueError as e:
        jobs.update_job(job_id, status="failed", error=str(e))
    except llm.AIError as e:
        jobs.update_job(job_id, status="failed", error=f"AI 识别失败：{e}")
    except Exception as e:  # 后台任务兜底：任何异常都落进任务状态，绝不让用户等到天荒地老
        jobs.update_job(job_id, status="failed", error=f"识别过程出错：{str(e)[:200]}")


@router.post("/{aid}/archive-article-image")
async def archive_article_image(
    aid: int,
    request: Request,
    file: UploadFile = File(...),
    url: str = Form(""),
):
    """上传推文页面长截图归档（唯一归档方式）：立即返回任务号，识别在后台异步进行，
    用户可离开页面；前端凭 /jobs/{jid} 轮询并在完成时通知。url 必填（记为 article_url）。"""
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "请先粘贴已发布的微信文章链接")
    ext = pathlib.Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(400, f"不支持的截图类型：{ext or '（无后缀）'}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "截图内容为空")
    if len(data) > ARTICLE_IMAGE_MAX:
        raise HTTPException(400, "截图超过 20MB 上限")
    with db.get_conn() as conn:
        _activity_or_404(conn, aid)

    rel_dir = f"storage/uploads/act_{aid}/article"
    dest_dir = db.ROOT / rel_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = dest_dir / f"{uuid.uuid4().hex}{ext}"
    src.write_bytes(data)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO uploads(activity_id, category, filename, stored_path, mime, size, caption)"
            " VALUES(?, 'article', ?, ?, ?, ?, '推文归档截图')",
            (aid, src.name, f"{rel_dir}/{src.name}", file.content_type or "image/jpeg", len(data)),
        )

    with db.get_conn() as conn:
        act = _activity_or_404(conn, aid)
    actor = auth.current_member(request)
    job_id = jobs.create_job("archive_article", ref_id=aid, label=act["name"], op_id=actor["id"])
    asyncio.create_task(_run_archive_job(job_id, aid, src, url, actor["id"]))
    return {"job_id": job_id, "status": "pending"}


async def _run_material_enhance(job_id: int, mid: int) -> None:
    """后台补全素材要点（形式/亮点/参与）并重建 wiki；完成/无需补全都折算成任务状态。"""
    jobs.update_job(job_id, status="running")
    try:
        await enhance_material(mid)
        with db.get_conn() as conn:
            row = conn.execute("SELECT name FROM materials WHERE id=?", (mid,)).fetchone()
        name = row["name"] if row else ""
        jobs.update_job(job_id, status="done", label=name, result={"material_id": mid, "name": name})
    except Exception as e:
        jobs.update_job(job_id, status="failed", error=f"素材要点补全失败：{str(e)[:200]}")


@router.post("/{aid}/publish-confirm")
async def publish_confirm(aid: int, request: Request):
    """推文已发布并归档：粘贴文章链接（归档）后确认，活动进入已完成；
    归档推文同步收进素材库（article_url 去重，重复确认仅更新）。
    操作者取自服务端会话；素材要点补全作为异步作业执行，完成时全站通知。"""
    with db.get_conn() as conn:
        row = _activity_or_404(conn, aid)
        actor = auth.current_member(request)
        # 跳级守卫：只允许「推文发布」阶段归档；已归档的重复确认保持幂等
        if row["stage"] not in ("publish", "done"):
            raise HTTPException(400, "请先完成影像稿件并推进到「推文发布」阶段，再确认归档")
        if not (row["report_text"] or "").strip():
            raise HTTPException(400, "确认归档前需先撰写通讯稿（影像稿件阶段完成）")
        if not row["article_url"]:
            raise HTTPException(400, "请先粘贴已发布的微信文章链接并完成归档")
        conn.execute(
            "UPDATE activities SET published_at=datetime('now','localtime'),"
            " stage='done', updated_at=datetime('now','localtime') WHERE id=?",
            (aid,),
        )
        _log(conn, f"完成了「{row['name']}」的项目归档（推文已发布）",
             op_id=actor["id"], aid=aid, action="publish")
        act = conn.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
        mid, inserted = db.upsert_material_from_activity(conn, act)
    wiki.rebuild()  # 归档推文入素材库后立即刷新 wiki
    if mid is not None:
        job_id = jobs.create_job("material_enhance", ref_id=mid, label="素材要点补全", op_id=actor["id"])
        asyncio.create_task(_run_material_enhance(job_id, mid))
    with db.get_conn() as conn:
        detail = _detail(conn, aid)
    detail["material_synced"] = inserted
    detail["material_id"] = mid
    return detail


REPORT_PHOTO_LIMIT = 8  # 多模态上下文里随消息附带的照片上限（用缩略图，每张几十 KB）


def _photo_data_urls(photos, limit: int = REPORT_PHOTO_LIMIT) -> list[str]:
    """把照片转成 data URL 供多模态输入：优先缩略图（480px JPEG），缺失时现生成；
    读取失败的照片跳过（不阻塞生成）。"""
    import base64 as _b64
    from ..thumbs import make_thumb
    urls: list[str] = []
    for p in photos:
        if len(urls) >= limit:
            break
        path = None
        if p["thumb_path"]:
            cand = db.ROOT / p["thumb_path"]
            if cand.exists():
                path = cand
        if path is None:
            tp = make_thumb(p["stored_path"])
            if tp:
                cand = db.ROOT / tp
                if cand.exists():
                    path = cand
            else:
                cand = db.ROOT / p["stored_path"]
                if cand.exists() and cand.stat().st_size <= 5 * 1024 * 1024:
                    path = cand  # 无缩略图时回退原图（限 5MB 内）
        if path is None:
            continue
        try:
            urls.append("data:image/jpeg;base64," + _b64.b64encode(path.read_bytes()).decode())
        except OSError:
            continue
    return urls


async def _run_report_generate(job_id: int, aid: int, op_id: int | None = None) -> None:
    """后台生成通讯稿（LLM 20-60 秒）：完成后服务端直接保存到 report_text（自动保存语义），
    任务 result 带全文供前端同步 textarea。现场照片以缩略图直接随消息喂给多模态模型。"""
    jobs.update_job(job_id, status="running")
    try:
        with db.get_conn() as conn:
            act = _detail(conn, aid)
            photos = conn.execute(
                "SELECT thumb_path, stored_path FROM uploads"
                " WHERE activity_id=? AND category='photo' ORDER BY id",
                (aid,),
            ).fetchall()
            # 范文：同类型优先，共两篇推文原文节选（学笔风与篇幅）
            rows = conn.execute(
                "SELECT raw_text FROM materials WHERE raw_text!=''"
                " ORDER BY (activity_type = ?) DESC, id DESC LIMIT 2",
                (act["activity_type"],),
            ).fetchall()
            samples = "\n——范文——\n".join((r["raw_text"] or "")[:900] for r in rows)

        plan_labels = (
            ("dispatch_note", "派发要点"), ("plan_note", "策划要点"), ("summary_note", "举办小结"),
        )
        plan_parts = [f"【{label}】{act[k]}" for k, label in plan_labels if (act[k] or "").strip()]
        plan_text = "\n".join(plan_parts)

        image_urls = _photo_data_urls(photos, limit=REPORT_PHOTO_LIMIT)
        photo_note = (
            f"【现场照片】共 {len(photos)} 张，其中 {len(image_urls)} 张已随本消息附上（多模态输入）。"
            if photos and image_urls
            else ("【现场照片】（暂无，或读取失败——不要虚构画面细节）" if not photos
                  else f"【现场照片】共 {len(photos)} 张，但本次读取失败——不要虚构画面细节。")
        )

        text = (await llm.chat(prompts.build_report_messages(act, samples, plan_text, photo_note, image_urls)) or "").strip()
        if not text:
            raise ValueError("AI 返回了空通讯稿，请重试")
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE activities SET report_text=?, report_updated_at=datetime('now','localtime'),"
                " updated_at=datetime('now','localtime') WHERE id=?",
                (text, aid),
            )
            _log(conn, f"用 AI 生成了「{act['name']}」的通讯稿", op_id=op_id, aid=aid, action="report")
        jobs.update_job(job_id, status="done", label=act["name"], result={"text": text, "activity_id": aid})
    except ValueError as e:
        jobs.update_job(job_id, status="failed", error=str(e))
    except llm.AIError as e:
        jobs.update_job(job_id, status="failed", error=f"生成失败：{e}")
    except Exception as e:
        jobs.update_job(job_id, status="failed", error=f"生成过程出错：{str(e)[:200]}")


@router.post("/{aid}/report/generate")
async def generate_report(aid: int, request: Request):
    """AI 一键生成通讯稿（异步作业）：参考素材库笔风/招募推文/策划链信息与照片清单；
    提交即返回任务号，完成后自动保存并全站通知。"""
    with db.get_conn() as conn:
        act = _activity_or_404(conn, aid)
    actor = auth.current_member(request)
    job_id = jobs.create_job("report_generate", ref_id=aid, label=act["name"], op_id=actor["id"])
    asyncio.create_task(_run_report_generate(job_id, aid, actor["id"]))
    return {"job_id": job_id, "status": "pending"}


async def _run_docx_export(job_id: int, aid: int, op_id: int | None) -> None:
    """后台构建 Word（含照片嵌入，通常数秒；大活动更久）：完成后任务 result 带文件名与大小。"""
    jobs.update_job(job_id, status="running")
    try:
        with db.get_conn() as conn:
            act = _activity_or_404(conn, aid)
            lead_name = _lead_names(conn, aid)
            photos = conn.execute(
                "SELECT stored_path, caption, filename FROM uploads WHERE activity_id=? AND category='photo' ORDER BY id",
                (aid,),
            ).fetchall()
            report = act["report_text"] or ""
        if not report.strip() and not photos:
            raise ValueError("还没有通讯稿或照片，先在「影像稿件」阶段补上再导出")
        exports_dir = db.ROOT / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        out = exports_dir / docx_report.out_filename(dict(act))
        docx_report.render(out, db.ROOT, dict(act), lead_name, report, photos)
        with db.get_conn() as conn:
            _log(conn, f"导出了「{act['name']}」的通讯稿 Word", op_id=op_id, aid=aid, action="export")
        jobs.update_job(job_id, status="done", label=act["name"],
                        result={"name": out.name, "size": out.stat().st_size, "activity_id": aid})
    except ValueError as e:
        jobs.update_job(job_id, status="failed", error=str(e))
    except Exception as e:
        jobs.update_job(job_id, status="failed", error=f"Word 生成失败：{str(e)[:200]}")


@router.post("/{aid}/export-docx")
async def build_export_docx(aid: int, request: Request):
    """异步构建通讯稿+照片的 Word（exports/ 留档）：立即返回任务号，前端轮询完成后走
    Range 断点下载。用户可离开页面，完成时全站通知。"""
    with db.get_conn() as conn:
        act = _activity_or_404(conn, aid)
    actor = auth.current_member(request)
    job_id = jobs.create_job("docx_export", ref_id=aid, label=act["name"], op_id=actor["id"])
    asyncio.create_task(_run_docx_export(job_id, aid, actor["id"]))
    return {"job_id": job_id, "status": "pending"}


@router.get("/{aid}/export-docx/download")
def download_export_docx(aid: int, name: str, request: Request):
    """下载已构建的 Word；支持 Range 断点续传（206 分段）。"""
    safe = pathlib.Path(name).name  # 防目录穿越
    path = (db.ROOT / "exports" / safe).resolve()
    if not path.is_relative_to((db.ROOT / "exports").resolve()) or not path.exists():
        raise HTTPException(404, "导出文件不存在（重新导出即可生成）")
    size = path.stat().st_size
    range_header = request.headers.get("range") or ""
    m = re.match(r"bytes=(\d+)-(\d*)$", range_header.strip())
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(safe),
    }
    if m:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        if start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        with open(path, "rb") as f:
            f.seek(start)
            chunk = f.read(end - start + 1)
        return Response(
            content=chunk, status_code=206, media_type=DOCX_MIME,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)},
        )
    return Response(
        content=path.read_bytes(), media_type=DOCX_MIME,
        headers={**headers, "Content-Length": str(size)},
    )


@router.delete("/{aid}")
def delete_activity(aid: int, request: Request):
    with db.get_conn() as conn:
        act = _activity_or_404(conn, aid)
        files = conn.execute("SELECT stored_path FROM uploads WHERE activity_id=?", (aid,)).fetchall()
        conn.execute("DELETE FROM expenses WHERE activity_id=?", (aid,))
        conn.execute("DELETE FROM activity_leads WHERE activity_id=?", (aid,))
        conn.execute("DELETE FROM uploads WHERE activity_id=?", (aid,))
        conn.execute("DELETE FROM activities WHERE id=?", (aid,))
        _log(conn, f"删除了活动「{act['name']}」", op_id=auth.require_manager(request)["id"], aid=None, action="delete")
    for f in files:
        path = db.ROOT / f["stored_path"]
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    shutil.rmtree(db.ROOT / "storage" / "uploads" / f"act_{aid}", ignore_errors=True)
    # 导出留档与作业记录一并清理（文件名含活动 id，前缀匹配安全）
    exports = db.ROOT / "exports"
    if exports.exists():
        for f in exports.glob(f"{docx_report._safe_name(act['name'])}-*-{aid}.docx"):
            try:
                f.unlink()
            except OSError:
                pass
    with db.get_conn() as conn:
        conn.execute("DELETE FROM jobs WHERE ref_id=? AND kind IN ('docx_export','archive_article','report_generate')", (aid,))
    return {"ok": True}


# ---- 材料购买与报销 ----

class ExpenseIn(BaseModel):
    item: str = ""
    amount: float = 0
    reimbursed: bool = False
    note: str = ""


@router.get("/{aid}/expenses")
def list_expenses(aid: int):
    with db.get_conn() as conn:
        _activity_or_404(conn, aid)
        rows = conn.execute(
            "SELECT * FROM expenses WHERE activity_id=? ORDER BY id DESC", (aid,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/{aid}/expenses")
def create_expense(aid: int, data: ExpenseIn):
    item = data.item.strip()
    if not item:
        raise HTTPException(400, "品名不能为空")
    with db.get_conn() as conn:
        _activity_or_404(conn, aid)
        cur = conn.execute(
            "INSERT INTO expenses(activity_id, item, amount, reimbursed, note) VALUES(?,?,?,?,?)",
            (aid, item, data.amount, 1 if data.reimbursed else 0, data.note.strip()),
        )
        row = conn.execute("SELECT * FROM expenses WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _expense_or_404(conn, eid: int):
    row = conn.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
    if row is None:
        raise HTTPException(404, "条目不存在")
    return row


expense_router = APIRouter(prefix="/api/expenses")


@expense_router.patch("/{eid}")
def update_expense(eid: int, data: ExpenseIn):
    item = data.item.strip()
    if not item:
        raise HTTPException(400, "品名不能为空")
    with db.get_conn() as conn:
        _expense_or_404(conn, eid)
        conn.execute(
            "UPDATE expenses SET item=?, amount=?, reimbursed=?, note=? WHERE id=?",
            (item, data.amount, 1 if data.reimbursed else 0, data.note.strip(), eid),
        )
        row = conn.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
    return dict(row)


@expense_router.delete("/{eid}")
def delete_expense(eid: int, request: Request):
    with db.get_conn() as conn:
        exp = _expense_or_404(conn, eid)
        conn.execute("DELETE FROM expenses WHERE id=?", (eid,))
        act = conn.execute("SELECT name FROM activities WHERE id=?", (exp["activity_id"],)).fetchone()
        _log(conn, f"删除了「{act['name'] if act else ''}」的报销条目「{exp['item']}」",
             op_id=auth.current_member(request)["id"], aid=exp["activity_id"], action="expense_delete")
    return {"ok": True}


# ---- 站点操作记录（谁做了什么）----

log_router = APIRouter(prefix="/api/log")


@log_router.get("")
def list_log(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    actor_id: int | None = None,
    activity_id: int | None = None,
):
    """操作记录分页 + 筛选（登录成员可见，保持团队透明）：返回 {total, has_more, items}。"""
    auth.current_member(request)
    conds: list[str] = []
    args: list = []
    if actor_id is not None:
        conds.append("l.actor_id=?")
        args.append(actor_id)
    if activity_id is not None:
        conds.append("l.activity_id=?")
        args.append(activity_id)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    lim = max(1, min(limit, 200))
    off = max(0, offset)
    with db.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM activity_log l{where}", args).fetchone()[0]
        rows = conn.execute(
            "SELECT l.id, l.actor_id, l.actor_name, l.activity_id, l.action, l.detail,"
            " l.created_at, a.name AS activity_name"
            " FROM activity_log l LEFT JOIN activities a ON a.id = l.activity_id"
            f"{where} ORDER BY l.created_at DESC, l.id DESC LIMIT ? OFFSET ?",
            (*args, lim + 1, off),
        ).fetchall()
    has_more = len(rows) > lim
    rows = rows[:lim]
    return {"total": total, "has_more": has_more, "items": [dict(r) for r in rows]}
