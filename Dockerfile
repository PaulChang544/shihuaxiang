# ---- 阶段一：构建前端 ----
# 国内网络可用构建参数换源：docker compose build \
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
# 子路径部署传 --build-arg VITE_BASE=/shihuaxiang/（挂在站点子路径下时）
FROM node:22-alpine AS frontend-build
ARG NPM_REGISTRY=https://registry.npmjs.org
ARG VITE_BASE=/
ENV VITE_BASE=${VITE_BASE}
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm config set registry ${NPM_REGISTRY} && npm install
COPY frontend/ ./
RUN npm run build

# ---- 阶段二：安装后端依赖并组装运行镜像 ----
# 国内网络可用构建参数换源：--build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple
FROM python:3.12-slim AS runtime
ARG PIP_INDEX=https://pypi.org/simple
ENV UV_DEFAULT_INDEX=${PIP_INDEX}
WORKDIR /app

RUN pip install --no-cache-dir uv -i ${PIP_INDEX}

# 先拷依赖清单以利用层缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 再拷源码与构建好的前端
COPY backend/ ./backend/
COPY tools/ ./tools/
COPY run.py ./
COPY --from=frontend-build /app/frontend/dist/ ./frontend/dist/

# 以非 root 运行（UID 1000 与部署指南的 chown 指引一致）
RUN useradd --uid 1000 --create-home appuser \
    && mkdir -p /app/storage /app/wiki \
    && chown -R appuser:appuser /app
USER appuser

# 数据目录由宿主机挂载（见 docker-compose.yml），容器内不留数据
ENV HOST=0.0.0.0 PORT=8321
EXPOSE 8321
# 直接用构建期装好的 venv 启动，运行时不联网、不依赖 uv
CMD ["/app/.venv/bin/python", "run.py"]
