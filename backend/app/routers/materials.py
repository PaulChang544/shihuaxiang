import asyncio
import pathlib

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from .. import article_shot, auth, db, jobs, llm, prompts
from ..agent import wiki
from ..schemas import MaterialInfo

router = APIRouter(prefix="/api/materials")


def _row_to_dict(r) -> dict:
    return dict(r)


class ExtractIn(BaseModel):
    text: str
    url: str = ""


async def extract_and_save(text: str, url: str = "", source_title: str = "") -> dict:
    """AI 提取推文信息并写入 materials 表，返回完整条目。粘贴正文与截图导入共用。"""
    messages = prompts.build_material_messages(text, url)
    raw = await llm.chat(messages, json_mode=True)
    try:
        info = llm.parse_model(raw, MaterialInfo)
    except llm.AIError:
        messages = messages + [
            AIMessage(content=raw),
            HumanMessage(content="你刚才的输出不是合法 JSON，请重新只输出一个 JSON 对象。"),
        ]
        info = llm.parse_model(await llm.chat(messages, json_mode=True), MaterialInfo)
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO materials(source_title, source_url, name, activity_type, time,"
            " form_points, highlights, participation, raw_text) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                source_title.strip() or info.name,
                url,
                info.name,
                info.activity_type,
                info.time,
                info.form_points,
                info.highlights,
                info.participation,
                text,
            ),
        )
        mid = cur.lastrowid
        row = conn.execute("SELECT * FROM materials WHERE id=?", (mid,)).fetchone()
    wiki.rebuild()  # 素材入库即重建 wiki 索引
    return dict(row)


async def _run_material_extract(job_id: int, text: str, url: str, source_title: str = "") -> None:
    """后台 AI 提取素材要点并入库（含 wiki 重建）。"""
    jobs.update_job(job_id, status="running")
    try:
        row = await extract_and_save(text, url, source_title)
        jobs.update_job(job_id, status="done", label=row["name"],
                        result={"material_id": row["id"], "name": row["name"]})
    except llm.AIError as e:
        jobs.update_job(job_id, status="failed", error=f"AI 提取失败：{e}")
    except Exception as e:
        jobs.update_job(job_id, status="failed", error=f"素材入库失败：{str(e)[:200]}")


async def _run_material_extract_image(job_id: int, data: bytes, url: str = "") -> None:
    """后台识别推文长截图（切片视觉转写）→ 提取素材要点 → 入库。

    截图不落盘：正文入库后原始图片即不再需要。非推文截图会被拒收。"""
    jobs.update_job(job_id, status="running")
    try:
        shot = await article_shot.transcribe(data)
    except ValueError as e:
        jobs.update_job(job_id, status="failed", error=str(e))
        return
    except llm.AIError as e:
        jobs.update_job(job_id, status="failed", error=f"AI 识别失败：{e}")
        return
    except Exception as e:
        jobs.update_job(job_id, status="failed", error=f"截图识别失败：{str(e)[:200]}")
        return
    await _run_material_extract(job_id, shot["text"], url, shot["title"])


@router.post("/extract")
async def extract_material(data: ExtractIn, request: Request):
    """粘贴正文入库：立即返回任务号，AI 提取在后台进行（可离开页面，完成时通知）。"""
    text = data.text.strip()
    if len(text) < 20:
        raise HTTPException(400, "推文正文太短，请粘贴完整正文")
    url = data.url.strip()
    if not url:
        raise HTTPException(400, "请先粘贴推文链接")
    actor = auth.current_member(request)
    job_id = jobs.create_job("material_extract", label="素材入库", op_id=actor["id"])
    asyncio.create_task(_run_material_extract(job_id, text, url))
    return {"job_id": job_id, "status": "pending"}


@router.post("/extract-image")
async def extract_material_image(
    request: Request,
    file: UploadFile = File(...),
    url: str = Form(""),
):
    """上传推文页面长截图导入（与活动归档同一识别链路）：立即返回任务号，
    整段识别 + 素材提取都在后台完成。url 必填（仅作为来源链接记录，不做抓取）。"""
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "请先粘贴推文链接")
    ext = pathlib.Path(file.filename or "").suffix.lower()
    if ext not in article_shot.IMAGE_EXTS:
        raise HTTPException(400, f"不支持的截图类型：{ext or '（无后缀）'}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "截图内容为空")
    if len(data) > article_shot.MAX_BYTES:
        raise HTTPException(400, "截图超过 20MB 上限")
    actor = auth.current_member(request)
    job_id = jobs.create_job("material_extract", label="截图素材入库", op_id=actor["id"])
    asyncio.create_task(_run_material_extract_image(job_id, data, url.strip()))
    return {"job_id": job_id, "status": "pending"}


async def enhance_material(mid) -> bool:
    """后台异步补全素材三字段（形式/亮点/参与），供归档入册后回填详细信息用。

    幂等：已有全部三字段、无正文、或找不到条目时跳过；无 AI 配置或调用失败静默放弃，
    不影响主流程。成功后重建 wiki 索引。"""
    if mid is None:
        return False
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT id, source_url, raw_text, activity_type, time, form_points, highlights, participation"
                " FROM materials WHERE id=?", (mid,)
            ).fetchone()
        if not row:
            return False
        text = (row["raw_text"] or "").strip()
        if not text:
            return False
        filled = all((row["form_points"], row["highlights"], row["participation"]))
        if filled:
            return False
        messages = prompts.build_material_messages(text, row["source_url"] or "")
        raw = await llm.chat(messages, json_mode=True)
        try:
            info = llm.parse_model(raw, MaterialInfo)
        except llm.AIError:
            messages = messages + [
                AIMessage(content=raw),
                HumanMessage(content="你刚才的输出不是合法 JSON，请重新只输出一个 JSON 对象。"),
            ]
            info = llm.parse_model(await llm.chat(messages, json_mode=True), MaterialInfo)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE materials SET form_points=?, highlights=?, participation=?,"
                " activity_type=?, time=? WHERE id=?",
                (
                    info.form_points or row["form_points"],
                    info.highlights or row["highlights"],
                    info.participation or row["participation"],
                    row["activity_type"] or info.activity_type,
                    row["time"] or info.time,
                    mid,
                ),
            )
        wiki.rebuild()
        return True
    except Exception as e:  # 后台任务：AI 不可用或配置缺失时放弃，不打断主流程
        print(f"[enhance_material] mid={mid} 补全失败：{e}")
        return False


def _material_update(info: dict, mid: int):
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE materials SET name=?, activity_type=?, time=?, form_points=?, highlights=?, participation=? WHERE id=?",
            (
                info.get("name"),
                info.get("activity_type"),
                info.get("time"),
                info.get("form_points"),
                info.get("highlights"),
                info.get("participation"),
                mid,
            ),
        )


@router.get("")
def list_materials():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM materials ORDER BY created_at DESC, id DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


class MaterialIn(BaseModel):
    name: str = ""
    activity_type: str = ""
    time: str = ""
    form_points: str = ""
    highlights: str = ""
    participation: str = ""


@router.put("/{mid}")
def update_material(mid: int, data: MaterialIn):
    _material_update(data.model_dump(), mid)
    wiki.rebuild()
    return {"ok": True}


@router.delete("/{mid}")
def delete_material(mid: int):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM materials WHERE id=?", (mid,))
    wiki.rebuild()
    return {"ok": True}


def summarize_for_types(conn, activity_types: list[str]) -> str:
    parts = []
    for t in activity_types:
        like = f"%{t}%"
        rows = conn.execute(
            "SELECT * FROM materials WHERE activity_type LIKE ? OR name LIKE ? LIMIT 4",
            (like, like),
        ).fetchall()
        for r in rows:
            parts.append(
                f"【{r['name']}】{r['time']}（{r['activity_type']}）形式：{r['form_points']}；"
                f"亮点：{r['highlights']}；参与：{r['participation']}"
            )
    return "\n".join(parts[:8])


def list_past_names(conn, limit: int = 40) -> list[str]:
    """近一年已办活动名称清单，供点子生成时避免重复。"""
    rows = conn.execute("SELECT name FROM materials ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [r["name"] for r in rows]
