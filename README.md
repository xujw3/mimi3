# mimi3 (mimo2api)

小米 AI Studio 自动化控制网关，将 MIMO 模型进行转发并兼容 OpenAI / Anthropic 常用接口。

## 功能

- OpenAI 兼容 API 中转：
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
  - `POST /v1/audio/speech`
  - `GET /v1/models`
- Anthropic 兼容 API 中转：
  - `POST /anthropic/v1/messages`
  - `GET /anthropic/v1/models`
- Web 控制面板：账号管理、节点状态、实时监控。
- 多账号轮询负载均衡。
- 流式响应支持。
- 模型名映射：可通过 `model_mapping.json` 或 WebUI/API 调整。
- 节点接入鉴权：支持 `MIMO_NODE_TOKEN` 保护 `/ws` 内网节点隧道。
- 指标持久化：请求成功率、状态码、延迟、token 用量、历史状态。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 复制并配置环境变量
cp env.example .env

# 编辑 .env，至少配置 WS_TUNNEL_URL
# 推荐同时配置 MIMO_NODE_TOKEN、MIMO_RELAY_OPENAI_KEY、MIMO_WEBUI_PASSWORD

# 启动服务
python main.py
```

默认服务端口为 `8000`，启动后访问：

```text
http://127.0.0.1:8000/webui
```

> Windows / Conda 环境下可显式指定 Python 解释器，例如：
>
> ```powershell
> & "D:\Program\anaconda3\envs\mimi\python.exe" main.py
> ```

## Docker 启动

```bash
cp env.example .env
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f mimi3
```

停止服务：

```bash
docker compose down
```

Docker Compose 会挂载以下本地目录：

- `./users` -> `/app/users`
- `./logs` -> `/app/logs`
- `./data` -> `/app/data`

容器内默认将指标数据库、指标快照、进程锁和模型映射文件放在 `/app/data`，对应宿主机 `./data` 目录：

```bash
MIMO_METRICS_DB_PATH=/app/data/gateway_metrics.db
MIMO_METRICS_SNAPSHOT_PATH=/app/data/gateway_snapshot.json
MIMO_PROCESS_LOCK_PATH=/app/data/mimo2api.lock
```

## 必需配置

一台拥有公网 IP 的机器，或者本机进行内网穿透。Claw 节点需要能反连本服务的 WebSocket 地址。

生产环境建议使用 HTTPS / WSS：

```bash
WS_TUNNEL_URL=wss://your-domain.com/ws
```

如果只是本地调试或内网测试，也可以使用：

```bash
WS_TUNNEL_URL=ws://your-domain.com:8000/ws
```

## 推荐安全配置

建议在 `.env` 中至少配置：

```bash
# 内网节点连接 /ws 的共享密钥；设置后 manager 注入 bridge 时会自动追加 token 参数
MIMO_NODE_TOKEN=replace-with-a-long-random-node-token

# 本机 OpenAI / Anthropic 兼容端点的 Bearer 密钥；不设置则 AI API 不鉴权
MIMO_RELAY_OPENAI_KEY=sk-your-random-secret-here

# WebUI 登录账号密码；不设置密码则 WebUI/API 管理面不启用登录鉴权
MIMO_WEBUI_USERNAME=admin
MIMO_WEBUI_PASSWORD=change-me

# WebUI 会话签名密钥，建议单独设置长随机字符串
MIMO_WEBUI_SECRET=replace-with-a-long-random-string

# HTTPS 反代后建议开启
MIMO_WEBUI_COOKIE_SECURE=true
```

### 鉴权说明

AI API 支持以下认证方式：

```http
Authorization: Bearer <MIMO_RELAY_OPENAI_KEY>
```

或：

```http
x-api-key: <MIMO_RELAY_OPENAI_KEY>
api-key: <MIMO_RELAY_OPENAI_KEY>
```

`/ws` 节点接入支持：

```text
wss://your-domain.com/ws?token=<MIMO_NODE_TOKEN>
```

也支持 header：

```http
Authorization: Bearer <MIMO_NODE_TOKEN>
x-node-token: <MIMO_NODE_TOKEN>
```

## 使用 API

### OpenAI Chat Completions

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-your-random-secret-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-v2.5-pro",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### OpenAI Responses API

```bash
curl http://127.0.0.1:8000/v1/responses \
  -H "Authorization: Bearer sk-your-random-secret-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-v2.5-pro",
    "input": "你好",
    "stream": true
  }'
```

### Anthropic Messages API

```bash
curl http://127.0.0.1:8000/anthropic/v1/messages \
  -H "Authorization: Bearer sk-your-random-secret-here" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-v2.5-pro",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### TTS

```bash
curl http://127.0.0.1:8000/v1/audio/speech \
  -H "Authorization: Bearer sk-your-random-secret-here" \
  -H "Content-Type: application/json" \
  -o speech.wav \
  -d '{
    "model": "tts-1",
    "voice": "alloy",
    "input": "你好，欢迎使用 mimi3。",
    "response_format": "wav"
  }'
```

## WebUI 使用

启动后访问：

```text
http://127.0.0.1:8000/webui
```

WebUI 可用于：

- 查看当前内网节点连接数量。
- 查看账号 Claw 状态和剩余时间。
- 导入账号 Cookie / Token 文本。
- 删除账号。
- 触发重建。
- 查看指标和状态历史。

账号会保存到：

```text
users/user_<userId>.json
```

该目录包含敏感凭证，不要提交到仓库。

## 模型映射

默认模型映射文件：

```text
model_mapping.json
```

Docker 环境中会初始化并持久化到：

```text
/app/data/model_mapping.json
```

可通过 API 获取或更新：

```bash
# 获取映射
curl http://127.0.0.1:8000/api/model_mapping

# 覆盖映射
curl -X PUT http://127.0.0.1:8000/api/model_mapping \
  -H "Content-Type: application/json" \
  -d '{"gpt-5.5": "mimo-v2.5-pro"}'
```

## 运行测试

当前仓库使用标准库 `unittest`，不依赖 pytest。

```bash
python -m compileall main.py mimo2api tests
python -m unittest discover -s tests
```

Windows / Conda 环境示例：

```powershell
& "D:\Program\anaconda3\envs\mimi\python.exe" -m compileall main.py mimo2api tests
& "D:\Program\anaconda3\envs\mimi\python.exe" -m unittest discover -s tests
```

## GitHub Actions 镜像发布

仓库包含：

```text
.github/workflows/docker-publish.yml
```

会在以下情况构建并推送多架构 Docker 镜像：

- 推送到 `master` / `main`
- 推送 `v*.*.*` 标签
- 手动触发 `workflow_dispatch`

推送目标：

- GHCR：`ghcr.io/<owner>/<repo>`
- Docker Hub：`docker.io/<DOCKERHUB_USERNAME>/<repo>`

可用仓库变量覆盖 Docker Hub 镜像仓库名：

```text
DOCKERHUB_REPOSITORY
```

需要在 GitHub 仓库 Secrets 中配置：

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
```

GHCR 使用 GitHub 自带 `GITHUB_TOKEN`，workflow 已配置：

```yaml
permissions:
  contents: read
  packages: write
```

## 常用环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `SERVER_HOST` | 服务监听地址 | `0.0.0.0` |
| `SERVER_PORT` | 服务端口 | `8000` |
| `WS_TUNNEL_URL` | Claw 节点反连网关的 WebSocket 地址 | `ws://<host>:<port>/ws` |
| `MIMO_NODE_TOKEN` | `/ws` 节点接入密钥 | 空 |
| `MIMO_RELAY_OPENAI_KEY` | AI API Bearer/API-key 鉴权密钥 | 空 |
| `MIMO_WEBUI_USERNAME` | WebUI 用户名 | `admin` |
| `MIMO_WEBUI_PASSWORD` | WebUI 密码；不设置则不启用 WebUI 鉴权 | 空 |
| `MIMO_WEBUI_SECRET` | WebUI session 签名密钥 | 回退到 WebUI 密码或 AI key |
| `MIMO_WEBUI_SESSION_TTL_SECONDS` | WebUI 会话有效期 | `43200` |
| `MIMO_WEBUI_COOKIE_SECURE` | Cookie 是否启用 Secure | `false` |
| `MIMO_METRICS_DB_PATH` | 指标历史 SQLite 路径 | `gateway_metrics.db` |
| `MIMO_METRICS_SNAPSHOT_PATH` | 累积指标快照路径 | `gateway_snapshot.json` |
| `MIMO_PROCESS_LOCK_PATH` | 单进程锁路径 | `mimo2api.lock` |
| `MIMO_NODE_401_COOLDOWN_SECONDS` | 节点 401 后冷却时间 | `900` |
| `MIMO_TTS_VOICE_MAP` | TTS voice JSON 映射覆盖 | 空 |

## 免责声明

1. **本项目仅供学习交流使用，禁止一切商业/滥用行为。**
2. 本项目为个人独立开发的开源项目，与小米公司及其关联方**无任何隶属、授权或合作关系**。
3. MIMO、Xiaomi AI Studio 等名称及商标归小米公司所有，本项目不主张任何权利。
4. 本项目不提供任何小米账号、密钥或付费服务的破解，仅作为技术研究用途。
5. 使用者应遵守所在地法律法规及小米服务条款，因使用本项目产生的一切后果由使用者自行承担。
6. 本项目代码随缘更新，作者不提供任何保证或技术支持。
7. **建议优先使用小米官方 API**，本项目仅为技术研究备选方案。
8. 如有任何权益问题，请联系删除。

## 致谢

[linux.do](https://linux.do)
