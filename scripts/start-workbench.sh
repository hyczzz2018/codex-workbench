#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_PROJECTS_DIR="$(cd "$DEFAULT_APP_DIR/.." && pwd)"

APP_DIR="${CODEX_WORKBENCH_APP_DIR:-$DEFAULT_APP_DIR}"
HOST="${CODEX_WORKBENCH_HOST:-127.0.0.1}"
PORT="${CODEX_WORKBENCH_PORT:-8010}"
LOG_PATH="${CODEX_WORKBENCH_LOG_PATH:-/tmp/codex-workbench.log}"
PYTHON_BIN="${CODEX_WORKBENCH_PYTHON:-$APP_DIR/.venv/bin/python}"
UVICORN_BIN="${CODEX_WORKBENCH_UVICORN:-$APP_DIR/.venv/bin/uvicorn}"
PID_PATTERN="$UVICORN_BIN app.main:app --host $HOST --port $PORT --app-dir $APP_DIR"

mkdir -p "$(dirname "$LOG_PATH")"
touch "$LOG_PATH"

old_pids="$(pgrep -f "$PID_PATTERN" || true)"
if [ -n "$old_pids" ]; then
  echo "[codex-workbench] stopping old process: $old_pids" | tee -a "$LOG_PATH"
  kill $old_pids || true
  sleep 1
fi

if [ ! -x "$UVICORN_BIN" ]; then
  echo "[codex-workbench] uvicorn not found: $UVICORN_BIN" | tee -a "$LOG_PATH"
  echo "[codex-workbench] run: cd $APP_DIR && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" | tee -a "$LOG_PATH"
  exit 1
fi

export CODEX_WORKBENCH_LOG_PATH="$LOG_PATH"
export DEV_SHELF_ROOT="${DEV_SHELF_ROOT:-$DEFAULT_PROJECTS_DIR/dev-shelf}"
export DEV_SHELF_TOOLS_ROOT="${DEV_SHELF_TOOLS_ROOT:-$DEV_SHELF_ROOT}"

echo "[codex-workbench] starting http://$HOST:$PORT/" | tee -a "$LOG_PATH"
echo "[codex-workbench] log: $LOG_PATH" | tee -a "$LOG_PATH"
setsid -f "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT" --app-dir "$APP_DIR" >>"$LOG_PATH" 2>&1
sleep 1

if ! "$PYTHON_BIN" - <<PY
import json
import urllib.request

with urllib.request.urlopen("http://$HOST:$PORT/health", timeout=2) as response:
    payload = json.load(response)
    assert payload.get("status") == "ok", payload
PY
then
  echo "[codex-workbench] health check failed; see $LOG_PATH" | tee -a "$LOG_PATH"
  exit 1
fi

echo "[codex-workbench] ready: http://$HOST:$PORT/" | tee -a "$LOG_PATH"
