import os
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

ROOT = Path(__file__).parent
DIST = ROOT / "frontend" / "dist"

# 部署时可经环境变量覆盖：HOST=0.0.0.0 PORT=9000
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8321"))


def build_frontend():
    print("首次运行：构建前端（需要已安装 Node.js）…")
    subprocess.run("npm install", cwd=ROOT / "frontend", shell=True, check=True)
    subprocess.run("npm run build", cwd=ROOT / "frontend", shell=True, check=True)


def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    from backend.app import db
    db.init_db()
    if not DIST.exists():
        build_frontend()
    if HOST in ("127.0.0.1", "localhost"):
        threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("backend.app.main:app", host=HOST, port=PORT)
