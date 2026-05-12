from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import importlib.util
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas.dev_shelf import (
    DevShelfArtifactReviseRequest,
    DevShelfArtifact,
    DevShelfDirectoryCreateRequest,
    DevShelfDirectoryCreateResponse,
    DevShelfDirectoryEntry,
    DevShelfDirectoryList,
    DevShelfExecutionPacket,
    DevShelfGatewayAbortRequest,
    DevShelfGatewayArtifactPayload,
    DevShelfGatewayCandidateConfirmRequest,
    DevShelfGatewayCandidateReviseRequest,
    DevShelfGatewayControlResponse,
    DevShelfGatewayRegisterResultRequest,
    DevShelfGatewayRuntimeEvents,
    DevShelfGatewaySessionStatus,
    DevShelfGatewayStartRequest,
    DevShelfGatewayTranscript,
    DevShelfGatewayTranscriptMessage,
    DevShelfHumanGate,
    DevShelfHumanGateDecisionRequest,
    DevShelfModelConfig,
    DevShelfModelItem,
    DevShelfModelList,
    DevShelfModelConfigUpdateRequest,
    DevShelfModelProvider,
    DevShelfProjectCreateRequest,
    DevShelfProjectCreateResponse,
    DevShelfRunCancelRequest,
    DevShelfRouterResult,
    DevShelfRunDetail,
    DevShelfRunSummary,
)


DEFAULT_DEV_SHELF_ROOT = Path(__file__).resolve().parents[2].parent / "dev-shelf"
RUN_ID_RE = re.compile(r"^run_[a-z0-9_-]+$")
GATE_ID_RE = re.compile(r"^[a-z0-9_-]+$")
PACKET_RE = re.compile(r"^(?P<sequence>\d{4})-execution-packet\.json$")
PROJECT_DIRECTORY_NAME_RE = re.compile(r"^[^/\\\x00]+$")
SESSION_ID_RE = re.compile(r"^session-[a-zA-Z0-9_-]+$")
CANDIDATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
PI_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PI_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PI_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
PROJECT_SLUG_RE = re.compile(r"^[a-z0-9]+([_-][a-z0-9]+)*$")
CONFIRMATION_SUFFIX = "_approval"
CONFIRMATION_ARTIFACTS = {"existing_project_analysis", "spec", "reuse_decision", "execution_todo"}
ARTIFACT_PREVIEW_LIMIT = 64 * 1024
IMPLEMENTATION_RESULT_ARTIFACT_ID = "implementation_result"
QUICK_DEPLOY_GUIDE_ARTIFACT_ID = "quick_deploy_guide"
GATEWAY_EVENT_LIMIT_DEFAULT = 100
GATEWAY_EVENT_LIMIT_MAX = 1000
GATEWAY_START_MAX_TIMEOUT_SECONDS = 3600.0
GATEWAY_PROCESS_ABORT_GRACE_SECONDS = 3.0
STREAM_TERMINAL_STATUSES = {"completed", "failed"}
SSE_RETRY_MS = 3000
GATEWAY_STREAM_POLL_INTERVAL_SECONDS = 0.05
GATEWAY_STREAM_KEEP_ALIVE_SECONDS = 15.0
DEFAULT_GATEWAY_MODELS = {
    "openai-codex": "gpt-5.4",
    "deepseek": "deepseek-v4-pro",
}
PROVIDER_LABELS = {
    "openai-codex": "Codex",
    "deepseek": "DeepSeek",
}
PI_MODEL_ROW_RE = re.compile(r"\s{2,}")


@dataclass
class DevShelfGatewayLaunch:
    run_id: str
    process: subprocess.Popen[str]
    launch_id: str
    started_at: str
    log_path: Path
    command: list[str]
    gateway_session_id: str | None = None


class DevShelfRunNotFound(ValueError):
    pass


class DevShelfGateConflict(ValueError):
    pass


class DevShelfGatewayConflict(ValueError):
    pass


class DevShelfWorkflowConflict(ValueError):
    pass


class DevShelfProjectConflict(ValueError):
    pass


class DevShelfToolError(RuntimeError):
    pass


_STREAM_CLOSED = object()


class _GatewayStreamSubscription:
    def __init__(self) -> None:
        self.events: queue.Queue[Any] = queue.Queue()

    def publish(self, event: dict[str, Any]) -> None:
        self.events.put(event)

    def close(self) -> None:
        self.events.put(_STREAM_CLOSED)


class _GatewayStreamSession:
    def __init__(
        self,
        *,
        service: "DevShelfReadService",
        run_id: str,
        session_dir: Path,
        session_id: str,
        runtime_events_path: Path,
        poll_interval_seconds: float,
    ) -> None:
        self.service = service
        self.run_id = run_id
        self.session_dir = session_dir
        self.session_id = session_id
        self.runtime_events_path = runtime_events_path
        self.poll_interval_seconds = poll_interval_seconds
        self._lock = threading.Lock()
        self._subscribers: set[_GatewayStreamSubscription] = set()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._cursor = 0
        self._offset = self._initial_offset()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def subscribe(self) -> _GatewayStreamSubscription:
        subscriber = _GatewayStreamSubscription()
        with self._lock:
            if self._closed:
                subscriber.close()
                return subscriber
            self._subscribers.add(subscriber)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name=f"gateway-stream-{self.session_id}",
                    daemon=True,
                )
                self._thread.start()
        return subscriber

    def unsubscribe(self, subscriber: _GatewayStreamSubscription) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def _initial_offset(self) -> int:
        try:
            return self.runtime_events_path.stat().st_size
        except OSError:
            return 0

    def _publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.publish(event)

    def _close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for subscriber in subscribers:
            subscriber.close()

    def _run(self) -> None:
        try:
            while True:
                page = self.service._read_gateway_runtime_events_from_offset(
                    self.runtime_events_path,
                    cursor=self._cursor,
                    limit=GATEWAY_EVENT_LIMIT_MAX,
                    offset=self._offset,
                )
                self._offset = page.get("file_offset") if isinstance(page.get("file_offset"), int) else self._offset
                status = self.service._gateway_status_from_session(self.session_dir)
                normalized_page = self.service._normalize_gateway_stream_page(
                    {
                        "schema_version": "1.0",
                        "run_id": status.run_id,
                        "gateway_session_id": status.gateway_session_id,
                        "session_dir": status.session_dir,
                        "runtime_events_path": status.runtime_events_path,
                        **page,
                    }
                )
                sent_any = False
                for event in normalized_page.get("events", []):
                    if not isinstance(event, dict):
                        continue
                    sent_any = True
                    event_cursor = event.get("cursor")
                    if isinstance(event_cursor, int):
                        self._cursor = max(self._cursor, event_cursor)
                    self._publish(event)
                if page.get("has_more"):
                    continue
                if status.status in STREAM_TERMINAL_STATUSES and not sent_any:
                    break
                time.sleep(max(self.poll_interval_seconds, 0.02))
        except Exception as exc:  # pragma: no cover
            self._publish(
                {
                    "schema_version": "1.0",
                    "event_id": f"{self.session_id}:stream_hub:error",
                    "event_type": "error",
                    "cursor": self._cursor,
                    "runtime_sequence": self._cursor,
                    "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "run_id": self.run_id,
                    "gateway_session_id": self.session_id,
                    "payload": {"message": f"Gateway stream hub stopped: {exc}", "source": "workbench"},
                    "source": {"stream": "workbench", "kind": "stream_hub", "raw_type": "error"},
                }
            )
        finally:
            self._close()
