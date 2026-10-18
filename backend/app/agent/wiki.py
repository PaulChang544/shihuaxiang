"""文件式 wiki：从 materials 表生成 Markdown 知识库，并提供 agent 查询函数。"""

import pathlib
import re

from .. import db

WIKI_DIR = db.ROOT / "wiki"
_CLEAN = re.compile(r"[\\/:*?\"<>|「」“”‘’!！?？,，。.、\s]+")


def slugify(name: str) -> str:
    return _CLEAN.sub("-", name).strip("-")[:40]


def _page_md(row) -> str:
    return f"""# {row['name']}

- 类型：{row['activity_type']}
- 时间：{row['time'] or '未标注'}
- 来源：{row['source_url'] or '（无链接）'}

## 形式要点
{row['form_points'] or '（无）'}

## 亮点
{row['highlights'] or '（无）'}

## 参与情况
{row['participation'] or '（无）'}

## 推文原文
{row['raw_text'] or '（无）'}
"""


def _slug_for(conn, row) -> str:
    """slug 始终附活动 id 后缀：不同名称经 sanitize 后可能相同，避免后写覆盖前一条。"""
    _ = conn
    base = slugify(row["name"]) or "activity"
    return f"{base}-{row['id']}"


def build_wiki(conn, out_dir: pathlib.Path | None = None) -> dict:
    out = pathlib.Path(out_dir) if out_dir else WIKI_DIR
    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for old in pages_dir.glob("*.md"):
        old.unlink()

    rows = conn.execute("SELECT * FROM materials ORDER BY activity_type, id").fetchall()
    groups: dict[str, list[tuple[str, object]]] = {}
    for r in rows:
        slug = _slug_for(conn, r)
        (pages_dir / f"{slug}.md").write_text(_page_md(r), encoding="utf-8")
        groups.setdefault(r["activity_type"] or "其他", []).append((slug, r))

    lines = [
        "# 十号社区活动 wiki（近一年）",
        "",
        "> 用法：先读本索引；需要细节时读对应页面（pages/<slug>.md）；查关键词可用 wiki_search 工具。",
        "",
    ]
    for atype, items in groups.items():
        lines.append(f"## {atype}（{len(items)}）")
        for slug, r in items:
            lines.append(f"- [{r['name']}](pages/{slug}.md) · {r['time'] or '时间未标注'} · {(r['highlights'] or '')[:40]}")
        lines.append("")
    (out / "index.md").write_text("\n".join(lines), encoding="utf-8")
    written = len(list(pages_dir.glob("*.md")))
    return {"pages": written, "dir": str(out)}


def rebuild() -> None:
    """素材入库/更新/删除后调用：全量重建 index 与 pages（幂等）。
    构建出错不得影响入库主流程。"""
    try:
        with db.get_conn() as conn:
            build_wiki(conn)
    except Exception:
        import sys
        import traceback

        print("wiki rebuild failed:", file=sys.stderr)
        traceback.print_exc()


def wiki_index_text(limit: int = 4000) -> str:
    f = WIKI_DIR / "index.md"
    if not f.exists():
        return "（wiki 尚未构建，服务重启后随素材库自动生成）"
    return f.read_text(encoding="utf-8")[:limit]


def wiki_search(keyword: str, activity_type: str = "", limit: int = 5) -> list[dict]:
    like = f"%{keyword}%"
    sql = ("SELECT * FROM materials WHERE (name LIKE ? OR form_points LIKE ? OR highlights LIKE ?)")
    args: list = [like, like, like]
    if activity_type:
        sql += " AND activity_type = ?"
        args.append(activity_type)
    sql += " LIMIT ?"
    args.append(limit)
    with db.get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
        return [{"slug": _slug_for(conn, r), "name": r["name"],
                 "activity_type": r["activity_type"], "time": r["time"],
                 "highlights": r["highlights"]} for r in rows]


def wiki_read(slug: str) -> str:
    # slug 由模型经工具调用传入，必须严防路径穿越（如 ../../storage/storage.db 读取含 Key 的数据库）
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        return "无效的 slug（请用 wiki_search 检索正确 slug）"
    pages = (WIKI_DIR / "pages").resolve()
    f = (pages / f"{slug}.md").resolve()
    if not f.is_relative_to(pages):
        return "无效的 slug（请用 wiki_search 检索正确 slug）"
    if not f.exists():
        return f"未找到页面：{slug}（可用 wiki_search 检索正确 slug）"
    return f.read_text(encoding="utf-8")[:6000]
