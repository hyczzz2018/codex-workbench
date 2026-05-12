import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

app = FastAPI(title="Codex Workbench", version="0.1.0")
app.include_router(router)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
STARTED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
WORKBENCH_LOG_PATH = os.getenv("CODEX_WORKBENCH_LOG_PATH", "/tmp/codex-workbench.log")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "codex-workbench",
        "version": app.version,
        "started_at": STARTED_AT,
        "log_path": WORKBENCH_LOG_PATH,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")
