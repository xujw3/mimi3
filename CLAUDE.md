# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

mimi3（mimo2api）是一个 Python/FastAPI 网关，把小米 AI Studio / MIMO 的能力转发为 OpenAI 兼容接口，并提供 Web 控制面板。核心运行方式是：本地公网网关维护 `/ws` WebSocket 隧道，远端 Claw 环境运行注入的 `bridge.py` 反连本地网关，HTTP API 请求通过队列转发到可用节点并把响应/流式 chunk 回传给客户端。

## 常用命令

```powershell
# 本项目 Python 固定使用该解释器
$PY = "D:\\Program\\anaconda3\\envs\\mimi\\python.exe"

# 安装依赖
& $PY -m pip install -r requirements.txt

# 复制环境变量模板并编辑 WS_TUNNEL_URL 等配置
Copy-Item env.example .env

# 本地启动（推荐入口；会 load .env 并设置 MIMO2API_WS_URL）
& $PY main.py

# 语法/导入前的轻量检查（当前仓库没有测试套件或 lint 配置）
& $PY -m compileall main.py mimo2api

# Docker 构建并后台启动
docker compose up -d --build

# 查看容器日志 / 停止容器
docker compose logs -f mimi3
docker compose down
```

当前仓库没有 `pytest`、`ruff`、`mypy`、`black` 等配置，也没有 `tests/`。不要声称已运行单元测试或 lint；如需验证现有代码，优先运行 `python -m compileall main.py mimo2api`，或启动服务后用实际 HTTP/WebUI 流程做冒烟测试。

## 运行配置与数据文件

- `.env` 由 `env.example` 复制而来；`WS_TUNNEL_URL` 是远端 bridge 反连本地网关的关键配置。
- `SERVER_HOST`/`SERVER_PORT` 控制 FastAPI 监听地址，默认 `0.0.0.0:8000`。
- `MIMO_RELAY_OPENAI_KEY` 启用 AI API Bearer/API-key 鉴权；未设置则 `/v1/*` 和 `/anthropic/v1/*` 不鉴权。
- `MIMO_WEBUI_USERNAME`/`MIMO_WEBUI_PASSWORD` 启用 WebUI 管理 API 登录；未设置密码时 WebUI 管理面不鉴权。
- `MIMO_WEBUI_SECRET`、`MIMO_WEBUI_SESSION_TTL_SECONDS`、`MIMO_WEBUI_COOKIE_SECURE` 控制 WebUI session cookie。
- `MIMO_METRICS_DB_PATH`、`MIMO_METRICS_SNAPSHOT_PATH`、`MIMO_PROCESS_LOCK_PATH` 控制运行时持久化位置；Docker 默认放在 `/app/data` 并映射到宿主机 `./data`。
- `users/user_*.json` 保存账号 cookie/token 数据，`logs/` 和 `data/` 是运行时目录，均不应提交。
- `model_mapping.json` 是模型名映射表；运行时也可通过 `/api/model_mapping` GET/PUT/DELETE 修改。

## 架构要点

- `main.py` 是推荐启动入口：加载 `.env`，把 `WS_TUNNEL_URL` 写入 `MIMO2API_WS_URL`，然后运行 `mimo2api.web_service:app`。不要随意改为直接 `uvicorn mimo2api.web_service:app`，否则 manager 注入 bridge 时可能拿不到 `MIMO2API_WS_URL`。
- `mimo2api/web_service.py` 是核心 FastAPI 应用：
  - lifespan 中获取单进程锁，初始化指标库，启动账号 manager、指标落库 worker、悬挂队列清理 worker。
  - `/ws` 接收远端 bridge 节点连接，并维护 `state.active_clients`、请求队列和 WebSocket/request 双向绑定。
  - `/v1/chat/completions`、`/anthropic/v1/messages` 走统一 `_forward_request()` 转发逻辑。
  - `/v1/responses` 使用 Responses API 转换器映射到 Chat Completions，再把响应转换回 Responses 格式。
  - `/v1/audio/speech` 把 OpenAI TTS 请求映射到 MIMO TTS payload，并从上游响应中提取 base64 音频。
  - `/v1/models`、`/anthropic/v1/models` 返回静态模型列表；`/api/stats`、`/api/status/history`、`/api/errors` 提供监控数据。
- `mimo2api/manager.py` 管理账号生命周期：扫描 `users/user_*.json`，每个账号创建 `AccountManager`，连接小米 AI Studio Claw，必要时销毁/创建/复用实例，并把本地 `bridge.py` 内容注入远端后台运行；多账号启动和重建会错峰。
- `mimo2api/bridge.py` 是注入到远端 Claw 环境运行的脚本：读取远端环境变量 `MIMO_API_KEY`、`MIMO_API_ENDPOINT`，将来自本地网关的请求转发到 MIMO/OpenAI/Anthropic 上游接口，再通过 WebSocket 返回 `start`/`chunk`/`finish`/`error` 消息。
- `mimo2api/gateway_state.py` 定义全局 `state` 单例，保存活跃节点、pending queues、冷却状态、指标和最近错误。该应用依赖进程内状态和 WebSocket 对象，不适合 `uvicorn --workers >1` 多进程运行。
- `mimo2api/metrics_store.py` 维护内存指标、SQLite `status_history`、累计指标快照，以及 `/api/stats` 使用的汇总结构。
- `mimo2api/auth.py` 负责 AI API key 鉴权和 WebUI HMAC session token；`mimo2api/ui_router.py` 提供 `/webui`、登录/登出、账号列表/新增/删除等管理接口。
- `mimo2api/responses_converter.py` 和 `mimo2api/audio_helpers.py` 是协议适配层；修改 `/v1/responses` 或 `/v1/audio/speech` 行为时优先在这些模块内保持转换逻辑内聚。
- `mimo2api/webui.html` 是单文件前端控制面板，通过 `/api/auth/session`、`/api/system/status`、`/api/users/*` 等接口与后端交互。

## 开发注意事项

- 转发链路的清理很重要：新增请求路径时要确保 `cleanup_pending_request()` 在正常结束、错误、超时和客户端/节点断开时都会执行，避免 pending queue 泄漏。
- 401/403/429 和 5xx 状态会触发重试/节点冷却；调整重试策略时同步考虑 `record_attempt_finished()`、`record_request_finished()` 和 `state.client_cooldowns`。
- 流式响应使用 keep-alive 与超时保护；修改 SSE 逻辑时保留 `STREAM_KEEPALIVE_INTERVAL`、`STREAM_CHUNK_TIMEOUT` 相关行为。
- Windows 与 Linux 都被支持：进程锁同时处理 `msvcrt` 和 `fcntl`，路径配置优先通过环境变量覆盖。
- Docker 镜像以非 root `app` 用户运行，入口脚本会创建并修正 `/app/users`、`/app/logs`、`/app/data` 权限，并把默认 `model_mapping.json` 初始化到持久化目录。
