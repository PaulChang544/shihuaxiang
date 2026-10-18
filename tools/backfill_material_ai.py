"""存量素材的 AI 补全回填：对「没有形式/亮点/参与但已有正文」的素材逐条跑 AI 提取。

发布归档新入库的素材由 publish-confirm 的后台任务自动补全；此工具用于一次性补齐
存量（含从旧版本/备份恢复回来的条目），或在服务器上手动补齐。幂等：字段已齐则跳过。

用法：uv run python tools/backfill_material_ai.py [--dry-run]
"""

import argparse
import asyncio
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.routers.materials import enhance_material  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(ROOT / "storage" / "storage.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, raw_text, form_points, highlights, participation FROM materials"
        " WHERE TRIM(COALESCE(raw_text,'')) != ''"
        " AND (TRIM(COALESCE(form_points,'')) = ''"
        "   OR TRIM(COALESCE(highlights,'')) = ''"
        "   OR TRIM(COALESCE(participation,'')) = '')"
        " ORDER BY id"
    ).fetchall()
    conn.close()
    print(f"待补全素材 {len(rows)} 条")
    if not rows:
        return

    async def run():
        done = 0
        for r in rows:
            if args.dry_run:
                print(f"(预演) #{r['id']} {r['name'][:18]}")
                continue
            ok = await enhance_material(r["id"])
            print(("✓" if ok else "跳过/失败"), f"#{r['id']} {r['name'][:18]}")
            done += ok
            await asyncio.sleep(0.3)
        print(f"补全完成 {done}/{len(rows)}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
