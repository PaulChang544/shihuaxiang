import os
import pathlib
from contextlib import asynccontextmanager

from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import auth, db
from .agent import graph as agent_graph
from .agent import wiki
from .routers import activities, agent, ideas, jobs as jobs_router, materials, members, settings, uploads

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# 全站安全响应头（媒体预览本身需要 CSP 放行 blob/data；样式仍依赖内联令牌）
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时建库（幂等）并打开 LangGraph SQLite 检查点连接，供全局复用。"""
    db.init_db()
    await agent_graph.init_checkpointer()
    wiki.rebuild()  # 启动即依据素材库生成 wiki/（含已完成活动推文回填）
    yield


def create_app() -> FastAPI:
    # 生产默认不暴露 /docs、/openapi.json（本地排查可 ENABLE_DOCS=1 打开）
    docs_on = os.environ.get("ENABLE_DOCS") == "1"
    app = FastAPI(
        title="十号社区活动工作台",
        lifespan=lifespan,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        """API 请求体硬上限：上传分片 1MB、直传 20MB，留足余量后统一 25MB。"""
        if request.url.path.startswith("/api/"):
            try:
                length = int(request.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length > 25 * 1024 * 1024:
                return JSONResponse({"detail": "请求体过大"}, status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        """/api/* 默认必须登录（激活/健康检查除外）；非 GET 请求校验同源防 CSRF。
        登录成员挂到 request.state.member，路由只读会话，绝不信任客户端传的 id。"""
        path = request.url.path
        renew_token = None
        if path.startswith("/api/") and path not in auth.PUBLIC_PATHS:
            token = request.cookies.get(auth.COOKIE_NAME) or ""
            with db.get_conn() as conn:
                member, renewed = auth.resolve_session(conn, token)
            if member is None:
                return JSONResponse({"detail": "请先激活身份（会话已失效）"}, status_code=401)
            request.state.member = dict(member)
            if renewed:
                renew_token = token  # 滑动续期：随响应重发 Cookie，同步浏览器端 max_age
        if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != (request.headers.get("host") or ""):
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        response = await call_next(request)
        if renew_token and response.status_code < 400:
            auth.set_session_cookie(response, request, renew_token)
        return response

    app.include_router(agent.router)
    app.include_router(settings.router)
    app.include_router(materials.router)
    app.include_router(jobs_router.router)
    app.include_router(ideas.router)
    app.include_router(members.router)
    app.include_router(members.auth_router)
    app.include_router(activities.router)
    app.include_router(activities.expense_router)
    app.include_router(activities.log_router)
    app.include_router(uploads.router)
    app.include_router(uploads.uploads_router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        # 首页 HTML 不缓存：SPA 产物按内容哈希换名，index 必须每次最新，
        # 否则客户端（尤其手机浏览器）会长期吃旧包（新功能看起来“没生效”）
        @app.get("/index.html", include_in_schema=False)
        @app.get("/", include_in_schema=False)
        def spa_index():
            return FileResponse(
                dist / "index.html",
                media_type="text/html",
                headers={"Cache-Control": "no-store"},
            )

        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")

    return app


app = create_app()
