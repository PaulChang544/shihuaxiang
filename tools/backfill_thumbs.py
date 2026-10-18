"""存量照片缩略图回填：给所有还没有 thumb_path 的照片补生成缩略图。幂等。

用法：uv run python tools/backfill_thumbs.py
（服务器上在容器内执行：docker exec no10-workbench python tools/backfill_thumbs.py）"""

import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402
from backend.app.thumbs import make_thumb  # noqa: E402


def main():
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, stored_path FROM uploads WHERE category='photo' AND thumb_path=''"
        ).fetchall()
    print(f"待回填 {len(rows)} 张")
    ok = 0
    for r in rows:
        tp = make_thumb(r["stored_path"])
        if tp:
            with db.get_conn() as conn:
                conn.execute("UPDATE uploads SET thumb_path=? WHERE id=?", (tp, r["id"]))
            ok += 1
            print("✓", r["id"], tp[:60])
        else:
            print("×", r["id"], r["stored_path"][:60])
    print(f"完成 {ok}/{len(rows)}")


if __name__ == "__main__":
    main()
