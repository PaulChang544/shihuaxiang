"""从素材库生成 wiki：uv run python tools/build_wiki.py"""

import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import db            # noqa: E402
from backend.app.agent import wiki    # noqa: E402


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        result = wiki.build_wiki(conn)
    print(f"wiki 已生成：{result['pages']} 个页面 → {result['dir']}")


if __name__ == "__main__":
    main()
