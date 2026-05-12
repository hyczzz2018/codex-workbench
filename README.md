# dev-shelf Workbench

dev-shelf Workbench 是一个本地 FastAPI 网页应用，用来从需求创建 dev-shelf run、查看和确认中间产物，并以接近 CLI 协作的方式控制单次 pi-agent Gateway 运行。

当前页面定位是 **单人版任务工作台**：

- 网页可以输入需求并创建 dev-shelf run。
- 网页负责查看 dev-shelf 任务进度、下一步建议、待确认项和中间产物。
- 左侧产物列表可直接预览原文，右侧协作对话显示 Workbench、用户和 pi-agent 的关键交流。
- Gateway / pi-agent 可以在任意可运行 packet 上启动或中止，运行事件通过 SSE 增量同步到页面。
- 待确认产物支持确认、提交修改意见并重新生成当前产物。
- Gateway candidate 可以从网页确认或要求修改；execution 阶段结果可以登记为实现结果。
- 模型配置面板支持 provider、账号和模型下拉选择，配置写入本地 `.workbench/model-config.json`。

## 功能范围

当前 MVP 包含：

- dev-shelf run 列表。
- 从网页创建 run，调用 dev-shelf 现有 `scripts/dev_shelf_start_project.py`。
- 项目目录浏览和目录创建。
- 任务终止。
- 当前 run 进度展示。
- 待确认项展示、确认和修改意见提交。
- 中间产物列表，点击后弹窗预览原文。
- 安全的文本产物预览和 Gateway candidate 预览。
- 最新 execution packet 摘要。
- 最新 Gateway / pi-agent session 状态。
- Gateway runtime events 按 cursor 轮询展示。
- Gateway stream 通过 SSE 增量推送。
- Gateway transcript 聚合成协作聊天流，过滤工具原文，只保留工具摘要。
- assistant 文本支持逐字显示和 Markdown 渲染。
- Gateway result / event candidates 摘要。
- 网页启动 / 中止单次 Gateway。
- Gateway candidate confirm / revise。
- enter_stage packet 的网页继续推进。
- execution Gateway 完成后登记实现结果。
- Provider / account / model 配置。
- 中文状态映射。
- 5 秒轮询自动刷新。

当前刻意不做：

- 多 Gateway session 调度。
- 多用户、权限系统或远程部署安全模型。
- 数据库持久化；状态仍以 dev-shelf 文件系统为准。
- 完整 IDE 能力；真实文件修改仍由 pi-agent / coding agent 完成。

## 项目结构

```text
codex-workbench/
  app/
    api/          # FastAPI 路由
    schemas/      # API schema
    services/     # dev-shelf 读取、旧 session 兼容服务
    static/       # 单人版工作台 HTML/CSS/JS
    main.py       # FastAPI 入口
  tests/          # API、服务和静态页面测试
  pyproject.toml
```

## 环境准备

建议使用 Python 3.10+。

```bash
cd /home/hyc/projects/codex-workbench
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## 运行

默认读取 `/home/hyc/projects/dev-shelf` 作为 dev-shelf 根目录。

推荐使用固定启动脚本。脚本会先停止同一 host/port/app-dir 上的旧 `uvicorn` 进程，再启动新服务，并把日志固定写到 `/tmp/codex-workbench.log`。

```bash
cd /home/hyc/projects/codex-workbench
bash scripts/start-workbench.sh
```

默认地址：

```text
http://127.0.0.1:8010/
```

等价的 `uvicorn` 命令：

```bash
.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8010 \
  --app-dir /home/hyc/projects/codex-workbench \
  >> /tmp/codex-workbench.log 2>&1
```

健康检查：

```bash
curl -s http://127.0.0.1:8010/health
```

如果 dev-shelf 不在默认路径，可以设置：

```bash
DEV_SHELF_ROOT=/path/to/dev-shelf \
DEV_SHELF_TOOLS_ROOT=/path/to/dev-shelf \
bash scripts/start-workbench.sh
```

## API

主要接口：

- `POST /api/dev-shelf/runs`
- `GET /api/dev-shelf/runs`
- `GET /api/dev-shelf/directories`
- `POST /api/dev-shelf/directories`
- `GET /api/dev-shelf/runs/{run_id}`
- `POST /api/dev-shelf/runs/{run_id}/cancel`
- `POST /api/dev-shelf/runs/{run_id}/workflow/continue`
- `GET /api/dev-shelf/runs/{run_id}/gateway/latest`
- `GET /api/dev-shelf/runs/{run_id}/gateway/events?cursor=0&limit=100`
- `GET /api/dev-shelf/runs/{run_id}/gateway/stream`
- `GET /api/dev-shelf/runs/{run_id}/gateway/transcript`
- `GET /api/dev-shelf/runs/{run_id}/gateway/result`
- `GET /api/dev-shelf/runs/{run_id}/gateway/candidates`
- `GET /api/dev-shelf/models`
- `GET /api/dev-shelf/model-config`
- `POST /api/dev-shelf/model-config`

Gateway 控制接口：

- `POST /api/dev-shelf/runs/{run_id}/gateway/start`
- `POST /api/dev-shelf/runs/{run_id}/gateway/abort`
- `POST /api/dev-shelf/runs/{run_id}/gateway/register-result`
- `POST /api/dev-shelf/runs/{run_id}/gateway/candidates/{candidate_id}/confirm`
- `POST /api/dev-shelf/runs/{run_id}/gateway/candidates/{candidate_id}/revise`
- `POST /api/dev-shelf/runs/{run_id}/artifacts/{artifact_id}/revise`

`start` 会启动本地后台进程并调用 dev-shelf 现有 `scripts/dev_shelf_gateway.py`。同一个 Workbench 进程内，一个 run 同时只允许一个 Gateway 进程。

兼容保留接口：

- `/api/sessions*`
- `/ws/sessions/{session_id}`
- `POST /api/dev-shelf/runs/{run_id}/human-gates/{gate_id}/decision`

后端确认写回接口暂时保留用于兼容；当前网页不会调用它。

## 模型配置

模型配置面板从本地 pi 配置读取可用信息：

- OpenAI Codex provider 需要选择本地 pi 账号，例如 `default`、`a`、`b`。
- DeepSeek provider 不显示账号，只显示模型列表；API key 由 pi 自己读取 `~/.pi/agent/auth.json`。
- Workbench 只保存 provider、account 和 model 选择，不保存 API key。
- 轻量模式会在启动 Gateway 时追加 `--no-tools` 和 `--no-context-files`，用于快速验证连接；真实开发通常不要开启。

## 创建 run

网页创建 run 时不会直接写 `run-state.json`。后端会生成 intake JSON，并调用 dev-shelf 现有脚本：

```text
scripts/dev_shelf_start_project.py
```

该脚本负责创建：

- `docs/<project_slug>/requirement-draft.md`
- `runs/<run_id>/run-state.json`
- 首个 `packets/*-execution-packet.*`

如果填写项目路径，路径会进入 run-state 的 workspace。只有勾选“确认路径权限”时，workspace 会标记为 confirmed，并把项目路径加入 allowed write paths。

## 产物预览安全边界

artifact 预览由后端读取 dev-shelf 文件系统，当前规则：

- 只允许预览 dev-shelf root 内文件。
- 只支持 UTF-8 文本。
- 单个 artifact 最多读取 64 KiB。
- 超出 root、不可读或非文本内容会返回中文错误提示，不会暴露任意本地文件。

## 自动刷新

页面加载后会每 5 秒轮询：

- run 列表。
- 当前 run 状态。
- 待确认项。
- artifact 列表和当前预览内容。
- 最新 execution packet。
- 最新 Gateway session。
- Gateway runtime events 增量页。
- Gateway transcript。
- Gateway result / candidates 摘要。

Gateway 运行中会优先使用 SSE stream 获取实时事件；自动刷新作为状态和产物兜底。页面会尽量保留用户阅读位置，避免刷新时强制滚到聊天底部。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

只跑静态页面和 dev-shelf API 相关测试：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_static_workbench.py \
  tests/test_dev_shelf_api.py
```

检查前端脚本语法：

```bash
node --check app/static/app.js
```

### 工作台视图预览

![workbench](.\codex-workbench\assets\workbench.png)

