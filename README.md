# 十号社区 · 活动工作台

面向学生社区 / 社团的**活动全流程工作台**：以活动为单位，六道阶段串成一条流水线，配合 AI 辅助策划、通讯稿写作与推文归档。

**派发（负责人确认接单）→ 策划 → 采购报销 → 举办 → 影像稿件（照片 + 通讯稿）→ 推文发布归档**

## 功能一览

### 活动流水线
- 每个阶段一张操作面板；推进需在确认弹窗中确认（未保存内容随推进自动保存），并校验必要前置（负责人、策划要点、通讯稿、已归档文章）
- 首页三块速览：**进行中**（点击滚动到流水线）/ **已归档**（弹窗回看）/ **操作记录**（谁做了什么，可筛选分页）；DDL 提醒按“我负责的活动”展示
- 活动对所有登录成员可见；删除活动等敏感操作仅管理员及以上

### AI 能力
- **灵感页**：条件式点子生成 + Agent 对话式策划助手；可开启 **DeepSeek 联网搜索**（复用主 API Key）检索最新热词与案例，先真检索再生成
- **素材库**：上传往期推文**长截图**（或直接粘贴正文）导入，AI 提取「形式 / 亮点 / 参与」等要点；入库后自动重建 wiki 索引；归档推文自动收入素材库并在后台补全要点
- **通讯稿生成**：以素材库范文学习笔风，**现场照片以多模态图片随消息直接喂给模型**（按画面内容描写，而非文件名清单）；生成完成后后台保存并全站通知
- **推文归档**：上传**推文页面长截图**做归档——服务器用多模态模型逐段识别标题与正文（超长图自动纵向切片、重叠去重、防幻觉、非推文截图拒收）；识别为**异步任务**，可离开页面，完成时全站通知。全站**不抓取微信文章链接**（素材库同一链路，只认截图/正文）

### 影像与导出
- 照片上传前**本地压缩至约 5MB**（质量 / 最长边阶梯，透明图铺白底），压缩与上传共用一个微信式**瓷贴环形进度**
- **分片断点续传**（1MB 分片，XHR 字节进度；浏览器中断后重选同一批文件自动续传）
- **缩略图体系**：上传即生成 480px 缩略图，照片墙只加载缩略图（流量降低两个数量级）；点击弹窗查看原图
- 通讯稿 + 照片一键导出 **Word**（服务端后台构建，完成后自动断点下载，支持 Range 续传）

### 成员体系（QQ 群式四级）
| 角色 | 说明 |
|---|---|
| 超管 | 内置隐藏账号，继承全部权限，可指定任意人员为站长；AI 设置仅超管可见 |
| 站长 | 全站唯一，仅经转让获得；转让后原站长自动降为管理员 |
| 管理员 | 名册与邀请码、任免成员管理员（可自卸）、删除活动、查看操作记录 |
| 成员 | 参与活动全流程，查看全部活动与操作记录 |

- 邀请码按 API Key 架构管理：**HMAC-SHA256 散列存储**（pepper 不入库）、6 位十六进制、明文仅生成时展示一次、列表不回传尾号
- 登录态为**服务端会话（HttpOnly Cookie）**，邀请码不再保存在浏览器；会话 7 天**滑动续期**（活跃使用自动延长）
- 防爆破：claim 每 IP 5 次/分钟、20 次/小时，超限锁 15 分钟

## 技术栈

- **后端**：Python / FastAPI / 原生 sqlite3（无 ORM），依赖用 [uv](https://docs.astral.sh/uv/) 管理；LangChain + LangGraph（Agent）、httpx（联网搜索）、Pillow（图像）、python-docx（导出）
- **前端**：React 19 + Vite，自研 MyReactTheme 主题（莫兰迪 · 流光：液态玻璃 / 昼夜双模式 / 动效总开关）
- **部署**：Docker Compose 单容器，前后端同源

## 快速开始（本地）

```bash
uv sync                        # 安装后端依赖
uv run python run.py           # 一键启动（首次自动构建前端，浏览器打开 127.0.0.1:8321）
```

开发模式可分开跑：

```bash
uv run uvicorn backend.app.main:app --port 8321   # 后端
cd frontend && npm run dev                         # 前端 5173，/api 代理到 8321
```

**首次使用**：打开页面输入超管邀请码（默认 `admin`）激活；随后在「设置 → AI 接入」配置 API Key / 模型（默认 DeepSeek，可用环境变量覆盖）。

## 部署（Docker Compose）

```bash
# 建议在项目根目录准备 .env（密钥不进入镜像、不进 git）
cat > .env <<'EOF'
DEEPSEEK_API_KEY=sk-...
SUPER_CODE=你的强随机超管码
EOF
chmod 600 .env

docker compose up -d --build
```

国内网络构建可加速：

```bash
PIP_INDEX=https://mirrors.aliyun.com/pypi/simple NPM_REGISTRY=https://registry.npmmirror.com \
  VITE_BASE=/shihuaxiang/ PORT=8322 docker compose up -d --build
```

数据目录以宿主卷挂载，升级镜像不丢数据：`./storage`（SQLite 与上传文件）、`./wiki`（Agent 资料库）、`./exports`（Word 留档）。容器以 uid 1000 运行，首次挂载后如遇权限错误：

```bash
chown -R 1000:1000 storage wiki exports
```

**子路径部署（nginx）**要点：

```nginx
location = /shihuaxiang { return 301 /shihuaxiang/; }
location ^~ /shihuaxiang/ {
    proxy_pass http://127.0.0.1:8322/;   # 剥除前缀
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;   # HTTPS 下会话 Cookie 自动带 Secure
    client_max_body_size 25m;            # 与应用层请求体上限对齐
    proxy_read_timeout 300s;
}
```

## 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `HOST` / `PORT` | 应用监听地址与端口 | `127.0.0.1` / `8321` |
| `VITE_BASE` | 前端构建的部署子路径（如 `/shihuaxiang/`） | `/` |
| `DEEPSEEK_API_KEY` | AI 服务商 Key（优先于设置页保存值） | — |
| `SUPER_CODE` | **超管固定邀请码**（修改后旧码在下次启动时自动吊销） | `admin` |
| `INVITE_PEPPER` | 邀请码 HMAC pepper（建议与 SUPER_CODE 一起设为随机值） | 由 SUPER_CODE 派生 |
| `ENABLE_DOCS` | 设为 `1` 才开放 `/docs`、`/openapi.json` | 关闭 |
| `PIP_INDEX` / `NPM_REGISTRY` | 构建期镜像加速 | 官方源 |

## ⚠️ 安全须知（部署后必做）

1. **必须修改默认超管码**：`admin` 仅供首次体验；在 `.env` 设置 `SUPER_CODE`（强随机）并经 compose 注入容器。修改后旧码自动失效。
2. **AI 设置仅超管可见**；名册、邀请码、删除活动、操作记录等管理权限归管理员及以上。
3. **附件安全**：上传内容按真实字节嗅探（伪装成图片的 HTML/SVG 一律拒绝）；图片内联、票据强制下载；全站 `nosniff` + CSP + `frame-ancestors 'none'`。
4. **不抓取外部文章**：服务端不访问任何微信文章链接（无抓取/解析入口），推文正文只来自用户上传的长截图或粘贴文本，从根源上消除 SSRF 面。
5. **数据备份含隐私**：备份归档内包含数据库（可能含 API Key）、学生照片与票据，请限制目录权限（脚本已自动设为 600）并妥善保管。

## 运维工具（`tools/`）

| 脚本 | 用途 |
|---|---|
| `backup.py --keep 30` | SQLite 一致性快照 + uploads/wiki/exports 打包，自动轮转 |
| `build_wiki.py` | 由素材库重建 Agent wiki 索引 |
| `backfill_material_ai.py` | 存量素材 AI 补全「形式/亮点/参与」 |
| `backfill_thumbs.py` | 存量照片补生成缩略图 |
| `compress_photos.py [--dry-run]` | 存量照片按 5MB 阶梯批量压缩 |

## 项目结构

```
backend/app/
  main.py            # FastAPI 入口：安全响应头 / 会话鉴权与 CSRF 中间件 / 静态托管
  auth.py            # HttpOnly Cookie 会话（签发 / 校验 / 滑动续期 / 吊销）
  codes.py           # 超管码规范化 + 邀请码 HMAC 散列
  db.py              # SCHEMA 与幂等迁移（含历史数据回填）
  jobs.py            # 通用异步作业（识别 / 导出 / 素材提取，状态表 + 轮询）
  routers/           # members / activities / uploads / materials / ideas / agent / settings / jobs
  agent/             # LangGraph 策划助手、wiki 索引、DeepSeek 搜索
  filesec.py         # 上传内容真实类型嗅探
  thumbs.py          # 缩略图
  docx_report.py     # Word 通讯稿渲染
frontend/src/        # React SPA（pages / components / ui 主题组件 / hooks / styles）
tools/               # 备份与数据维护脚本
```

## 测试

```bash
uv run pytest          # 120 项：API、权限矩阵、会话与 CSRF、上传嗅探、截图导入、导入导出…
cd frontend && npm run build   # 前端构建
```

## License

[GPL-3.0](LICENSE) © 十号社区
