# dev-shelf Workbench

dev-shelf Workbench 是一个本地 FastAPI 网页应用，用来从需求创建 dev-shelf run，观察中间产物，并控制单次 pi-agent Gateway 运行。

当前页面定位是 **单人版任务工作台**：

- 网页可以输入需求并创建 dev-shelf run。
- 网页负责查看 dev-shelf 任务进度、待确认项、最新建议和中间产物。
- Gateway / pi-agent 放在执行详情中，用于执行阶段。
- 网页可以启动或中止一次 pi-agent Gateway。
- 网页端暂不提供 apply Gateway candidate。
- 网页端暂不提供 approve / reject / 确认 / 驳回等流程推进入口。

## 功能范围

当前 MVP 包含：

- dev-shelf run 列表。
- 从网页创建 run，调用 dev-shelf 现有 `scripts/dev_shelf_start_project.py`。
- 当前 run 进度展示。
- 待确认项只读展示。
- 中间产物列表。
- 安全的文本产物预览。
- 最新 execution packet 摘要。
- 最新 Gateway / pi-agent session 状态。
- Gateway runtime events 按 cursor 轮询展示。
- Gateway result / event candidates 摘要。
- 网页启动 / 中止单次 Gateway。
- 中文状态映射。
- 5 秒轮询自动刷新。

当前刻意不做：

- WebSocket / SSE 实时通道。
- 网页驱动终端 agent。
- 网页端人工确认或驳回。
- 网页端 apply Gateway candidate。
- 多 Gateway session 调度。
- 旧聊天框或旧 session 工作流 UI。
- 数据库持久化。

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

```bash
.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --app-dir /home/hyc/projects/codex-workbench
```

打开：

```text
http://127.0.0.1:8000/
```

如果 dev-shelf 不在默认路径，可以设置：

```bash
DEV_SHELF_ROOT=/path/to/dev-shelf \
DEV_SHELF_TOOLS_ROOT=/path/to/dev-shelf \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir /home/hyc/projects/codex-workbench
```

## API

主要接口：

- `POST /api/dev-shelf/runs`
- `GET /api/dev-shelf/runs`
- `GET /api/dev-shelf/runs/{run_id}`
- `GET /api/dev-shelf/runs/{run_id}/gateway/latest`
- `GET /api/dev-shelf/runs/{run_id}/gateway/events?cursor=0&limit=100`
- `GET /api/dev-shelf/runs/{run_id}/gateway/result`
- `GET /api/dev-shelf/runs/{run_id}/gateway/candidates`

Gateway 控制接口：

- `POST /api/dev-shelf/runs/{run_id}/gateway/start`
- `POST /api/dev-shelf/runs/{run_id}/gateway/abort`

`start` 第一版是本地后台进程壳，会调用 dev-shelf 现有 `scripts/dev_shelf_gateway.py`。同一个 Workbench 进程内，一个 run 同时只允许一个 Gateway 进程。

兼容保留接口：

- `/api/sessions*`
- `/ws/sessions/{session_id}`
- `POST /api/dev-shelf/runs/{run_id}/human-gates/{gate_id}/decision`

后端确认写回接口暂时保留用于兼容；当前网页不会调用它。

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
- Gateway result / candidates 摘要。

自动刷新失败时会保留当前已展示数据，等待下一轮恢复。

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
