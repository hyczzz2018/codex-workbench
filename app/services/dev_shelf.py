from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
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


DEFAULT_DEV_SHELF_ROOT = Path("/home/hyc/projects/dev-shelf")
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
GATEWAY_EVENT_LIMIT_DEFAULT = 100
GATEWAY_EVENT_LIMIT_MAX = 1000
GATEWAY_START_MAX_TIMEOUT_SECONDS = 3600.0
GATEWAY_PROCESS_ABORT_GRACE_SECONDS = 3.0
STREAM_TERMINAL_STATUSES = {"completed", "failed"}
SSE_RETRY_MS = 3000
DEFAULT_GATEWAY_MODELS = {
    "openai-codex": "gpt-5.4",
    "deepseek": "deepseek-v4-pro",
}
PROVIDER_LABELS = {
    "openai-codex": "Codex",
    "deepseek": "DeepSeek",
}


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


class DevShelfReadService:
    def __init__(
        self,
        root: Path | str | None = None,
        tools_root: Path | str | None = None,
    ) -> None:
        configured_root = root or os.getenv("DEV_SHELF_ROOT") or DEFAULT_DEV_SHELF_ROOT
        self.root = Path(configured_root).expanduser().resolve()
        configured_tools_root = tools_root or os.getenv("DEV_SHELF_TOOLS_ROOT") or DEFAULT_DEV_SHELF_ROOT
        self.tools_root = Path(configured_tools_root).expanduser().resolve()
        configured_projects_root = os.getenv("DEV_SHELF_PROJECTS_ROOT") or self.root.parent
        self.projects_root = Path(configured_projects_root).expanduser().resolve()
        self._gateway_launches: dict[str, DevShelfGatewayLaunch] = {}

    @property
    def workbench_config_dir(self) -> Path:
        return self.root / ".workbench"

    @property
    def model_config_path(self) -> Path:
        return self.workbench_config_dir / "model-config.json"

    @property
    def pi_agent_dir(self) -> Path:
        return Path.home() / ".pi" / "agent"

    @property
    def pi_auth_path(self) -> Path:
        return self.pi_agent_dir / "auth.json"

    @property
    def pi_settings_path(self) -> Path:
        return self.pi_agent_dir / "settings.json"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def list_runs(self) -> list[DevShelfRunSummary]:
        if not self.runs_dir.is_dir():
            return []

        runs: list[DevShelfRunSummary] = []
        for run_state_path in sorted(self.runs_dir.glob("*/run-state.json")):
            state = self._load_json(run_state_path)
            if not isinstance(state, dict):
                continue
            runs.append(self._summary_from_state(state))

        return sorted(
            runs,
            key=lambda item: item.updated_at or "",
            reverse=True,
        )

    def get_run(self, run_id: str) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)

        artifacts = [
            self._artifact_from_state_item(run_dir, item)
            for item in state.get("artifacts", [])
            if isinstance(item, dict) and item.get("artifact_id")
        ]
        summary = self._summary_from_state(state)
        latest_packet = self._latest_packet(run_dir)
        pending_gates = self._pending_human_gates(state)
        return DevShelfRunDetail(
            **summary.model_dump(),
            task_type_status=state.get("task_type_status"),
            artifacts=artifacts,
            pending_human_gates=pending_gates,
            router=self._router_result(state, latest_packet, pending_gates),
            latest_packet=latest_packet,
        )

    def create_project(self, payload: DevShelfProjectCreateRequest) -> DevShelfProjectCreateResponse:
        intake = self._project_intake_from_payload(payload)
        intake_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="codex-workbench-intake-",
                delete=False,
            ) as fh:
                intake_path = Path(fh.name)
                json.dump(intake, fh, ensure_ascii=False, indent=2)
                fh.write("\n")

            result = self._run_dev_shelf_tool(
                "dev_shelf_start_project.py",
                [
                    "--intake",
                    str(intake_path),
                    "--root",
                    str(self.root),
                    "--pretty",
                ],
            )
        finally:
            if intake_path is not None:
                try:
                    intake_path.unlink(missing_ok=True)
                except OSError:
                    pass

        if not result.get("run_id"):
            raise DevShelfToolError("dev-shelf start project did not return run_id")
        return DevShelfProjectCreateResponse(**result)

    def list_project_directories(self, path: str | None = None) -> DevShelfDirectoryList:
        current_path = self._resolve_project_directory(path, must_exist=True)
        if not current_path.is_dir():
            raise DevShelfProjectConflict(f"项目路径不是目录：{current_path}")

        items: list[DevShelfDirectoryEntry] = []
        try:
            children = list(current_path.iterdir())
        except OSError as exc:
            raise DevShelfProjectConflict(f"目录不可读取：{current_path}") from exc

        for child in sorted(children, key=lambda item: item.name.lower()):
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not child.is_dir() or not self._is_under_projects_root(resolved):
                continue
            items.append(DevShelfDirectoryEntry(name=child.name, path=str(resolved)))

        parent_path: str | None = None
        if current_path != self.projects_root:
            parent = current_path.parent.resolve()
            if self._is_under_projects_root(parent):
                parent_path = str(parent)

        return DevShelfDirectoryList(
            root_path=str(self.projects_root),
            current_path=str(current_path),
            parent_path=parent_path,
            items=items,
        )

    def create_project_directory(
        self,
        payload: DevShelfDirectoryCreateRequest,
    ) -> DevShelfDirectoryCreateResponse:
        parent_path = self._resolve_project_directory(payload.parent_path, must_exist=True)
        name = self._validate_new_directory_name(payload.name)
        candidate = (parent_path / name).resolve(strict=False)
        if not self._is_under_projects_root(candidate):
            raise DevShelfProjectConflict("目录必须创建在项目根目录内。")
        try:
            candidate.mkdir()
        except FileExistsError as exc:
            raise DevShelfProjectConflict(f"目录已存在：{candidate}") from exc
        except OSError as exc:
            raise DevShelfProjectConflict(f"目录创建失败：{candidate}") from exc
        return DevShelfDirectoryCreateResponse(path=str(candidate))

    def cancel_run(self, run_id: str, payload: DevShelfRunCancelRequest) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        if state.get("status") == "cancelled":
            return self.get_run(run_id)

        active = self._active_gateway_launch(run_id)
        if active is not None:
            self._mark_latest_gateway_abort_requested(run_id, active.gateway_session_id)
            self._stop_gateway_process(active.process)
            self._gateway_launches.pop(run_id, None)

        run_state_path = run_dir / "run-state.json"
        self._run_dev_shelf_tool(
            "dev_shelf_emit_event.py",
            [
                "stage",
                "--run-state",
                str(run_state_path),
                "--actor",
                "human",
                "--stage",
                str(state.get("current_stage") or "cancelled"),
                "--run-status",
                "cancelled",
                "--note",
                payload.note or "用户在网页终止任务。",
                "--apply",
                "--pretty",
            ],
        )
        return self.get_run(run_id)

    def continue_workflow(self, run_id: str) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        latest_packet = self._latest_packet(run_dir)
        if latest_packet is None or latest_packet.decision_type != "enter_stage":
            raise DevShelfWorkflowConflict("当前下一步不是流程推进。")
        if not latest_packet.path:
            raise DevShelfWorkflowConflict("当前流程推进 packet 缺少路径。")

        self._run_dev_shelf_tool(
            "dev_shelf_workflow_action.py",
            [
                "--pretty",
                "continue-enter-stage",
                "--run-state",
                str(run_dir / "run-state.json"),
                "--packet",
                latest_packet.path,
                "--apply",
            ],
        )
        return self.get_run(run_id)

    def get_gateway_status(self, run_id: str, session_id: str | None = None) -> DevShelfGatewaySessionStatus:
        run_dir = self._run_dir(run_id)
        if session_id is None:
            launch_status = self._gateway_launch_status(run_dir, run_id)
            if launch_status is not None:
                return launch_status
        session_dir = self._gateway_session_dir(run_dir, session_id)
        return self._gateway_status_from_session(session_dir)

    def get_gateway_events(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        cursor: int | str | None = None,
        limit: int | str | None = None,
    ) -> DevShelfGatewayRuntimeEvents:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        status = self._gateway_status_from_session(session_dir)
        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfRunNotFound(f"Gateway runtime events not found: {run_id}")
        page = self._read_gateway_runtime_events(runtime_events_path, cursor=cursor, limit=limit)
        return DevShelfGatewayRuntimeEvents(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            session_dir=status.session_dir,
            runtime_events_path=status.runtime_events_path,
            **page,
        )

    def iter_gateway_stream_events(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        cursor: int | str | None = None,
        limit: int | str | None = None,
        last_event_id: str | None = None,
        poll_interval_seconds: float = 0.5,
    ):
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        status = self._gateway_status_from_session(session_dir)
        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfRunNotFound(f"Gateway runtime events not found: {run_id}")

        current_cursor = self._gateway_stream_start_cursor(cursor, last_event_id)
        actual_limit = self._normalize_gateway_limit(limit)
        yield f"retry: {SSE_RETRY_MS}\n\n"

        while True:
            page = self._read_gateway_runtime_events(
                runtime_events_path,
                cursor=current_cursor,
                limit=actual_limit,
            )
            normalized_page = self._normalize_gateway_stream_page(
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
                    current_cursor = max(current_cursor, event_cursor)
                yield self._sse_encode_event(event)

            if page.get("has_more"):
                continue

            status = self._gateway_status_from_session(session_dir)
            if status.status in STREAM_TERMINAL_STATUSES:
                break

            if not sent_any:
                yield ": keep-alive\n\n"
            time.sleep(max(poll_interval_seconds, 0.1))

    def get_gateway_transcript(
        self,
        run_id: str,
        session_id: str | None = None,
    ) -> DevShelfGatewayTranscript:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        status = self._gateway_status_from_session(session_dir)
        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfRunNotFound(f"Gateway runtime events not found: {run_id}")
        messages, event_count = self._read_gateway_transcript(runtime_events_path)
        return DevShelfGatewayTranscript(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            session_dir=status.session_dir,
            runtime_events_path=status.runtime_events_path,
            message_count=len(messages),
            event_count=event_count,
            messages=messages,
        )

    def get_gateway_result(self, run_id: str, session_id: str | None = None) -> DevShelfGatewayArtifactPayload:
        return self._gateway_artifact_payload(
            run_id,
            session_id,
            metadata_field="gateway_result_json",
            default_name="gateway-result.json",
        )

    def get_gateway_candidates(self, run_id: str, session_id: str | None = None) -> DevShelfGatewayArtifactPayload:
        return self._gateway_artifact_payload(
            run_id,
            session_id,
            metadata_field="gateway_event_candidates_json",
            default_name="gateway-event-candidates.json",
            preview_candidates=True,
        )

    def start_gateway(
        self,
        run_id: str,
        payload: DevShelfGatewayStartRequest,
    ) -> DevShelfGatewayControlResponse:
        run_dir = self._run_dir(run_id)
        active = self._active_gateway_launch(run_id)
        if active is not None:
            raise DevShelfGatewayConflict(f"Gateway is already running for run: {run_id}")

        self._ensure_gateway_runnable(run_dir)
        command = self._gateway_start_command(run_id, payload)
        launch_id = f"launch-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        started_at = self._utc_now()
        log_dir = run_dir / "artifacts" / "pi-agent-gateway-launches"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{launch_id}.log"
        process = self._spawn_gateway_process(command, log_path)
        launch = DevShelfGatewayLaunch(
            run_id=run_id,
            process=process,
            launch_id=launch_id,
            started_at=started_at,
            log_path=log_path,
            command=command,
        )
        self._gateway_launches[run_id] = launch

        if process.poll() is not None:
            self._gateway_launches.pop(run_id, None)
            detail = self._read_log_tail(log_path) or f"Gateway exited immediately with code {process.returncode}"
            raise DevShelfToolError(detail)

        return self._gateway_control_response(launch, status="started", message="Gateway 已启动。")

    def abort_gateway(
        self,
        run_id: str,
        payload: DevShelfGatewayAbortRequest | None = None,
    ) -> DevShelfGatewayControlResponse:
        self._run_dir(run_id)
        requested_session_id = payload.gateway_session_id if payload else None
        if requested_session_id and not SESSION_ID_RE.fullmatch(requested_session_id):
            raise DevShelfRunNotFound(f"Invalid Gateway session id: {requested_session_id}")

        active = self._active_gateway_launch(run_id)
        if active is None:
            return DevShelfGatewayControlResponse(
                run_id=run_id,
                status="not_running",
                gateway_session_id=requested_session_id,
                message="当前 Workbench 进程没有记录到正在运行的 Gateway。",
            )

        if requested_session_id and active.gateway_session_id and requested_session_id != active.gateway_session_id:
            raise DevShelfGatewayConflict(f"Gateway session is not running: {requested_session_id}")

        self._mark_latest_gateway_abort_requested(run_id, requested_session_id)
        returncode = self._stop_gateway_process(active.process)
        self._gateway_launches.pop(run_id, None)
        status = "aborted" if returncode is not None else "abort_requested"
        return self._gateway_control_response(
            active,
            status=status,
            returncode=returncode,
            message="Gateway 中止请求已发送。",
        )

    def register_gateway_result(
        self,
        run_id: str,
        payload: DevShelfGatewayRegisterResultRequest,
    ) -> DevShelfRunDetail:
        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        existing = self._find_artifact(state, "implementation_result")
        if existing and existing.get("status") not in {None, "", "missing"}:
            return self.get_run(run_id)

        session_dir = self._gateway_session_dir(run_dir, payload.session_id)
        status = self._gateway_status_from_session(session_dir)
        if status.status != "completed":
            raise DevShelfGatewayConflict("pi-agent 还没有完成，不能登记实现结果。")

        latest_packet = self._latest_packet(run_dir)
        if not self._is_execution_gateway_result(status, latest_packet):
            raise DevShelfGatewayConflict("当前 Gateway session 不是 execution 阶段结果，不能登记为实现结果。")

        runtime_events_path = Path(status.runtime_events_path) if status.runtime_events_path else None
        if runtime_events_path is None or not runtime_events_path.is_file():
            raise DevShelfGatewayConflict("Gateway runtime events 不存在，无法生成实现结果。")

        target_path = self._implementation_result_path(run_dir, latest_packet, status)
        if target_path is None:
            raise DevShelfGatewayConflict("当前 execution packet 没有 implementation_result 输出路径。")

        messages, _event_count = self._read_gateway_transcript(runtime_events_path)
        content = self._render_gateway_implementation_result(
            run_id=run_id,
            status=status,
            messages=messages,
            target_path=target_path,
            note=payload.note,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")

        run_state_path = run_dir / "run-state.json"
        self._run_dev_shelf_tool(
            "dev_shelf_emit_event.py",
            [
                "artifact",
                "--run-state",
                str(run_state_path),
                "--actor",
                "ai",
                "--artifact-id",
                "implementation_result",
                "--artifact-status",
                "done",
                "--title",
                "实现结果",
                "--path",
                str(target_path),
                "--produced-by",
                "stage.execution",
                "--note",
                payload.note or "Workbench 已将本轮 pi-agent 执行结果登记为 implementation_result。",
                "--apply",
                "--pretty",
            ],
        )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)

    def confirm_gateway_candidate(
        self,
        run_id: str,
        candidate_id: str,
        payload: DevShelfGatewayCandidateConfirmRequest,
    ) -> DevShelfRunDetail:
        if not CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise DevShelfGatewayConflict(f"Invalid Gateway candidate id: {candidate_id}")

        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, payload.session_id)
        status = self._gateway_status_from_session(session_dir)
        if status.status != "completed":
            raise DevShelfGatewayConflict("pi-agent 未成功完成，不能确认本轮候选产物。")
        candidate_path = self._gateway_candidate_path(session_dir)
        candidate = self._gateway_candidate_from_file(candidate_path, candidate_id)
        event = candidate.get("event") if isinstance(candidate.get("event"), dict) else None
        if not event or not event.get("artifact_id"):
            raise DevShelfGatewayConflict(f"Gateway candidate is not confirmable: {candidate_id}")

        run_state_path = run_dir / "run-state.json"
        self._run_dev_shelf_tool(
            "dev_shelf_workflow_action.py",
            [
                "--pretty",
                "apply-candidate",
                "--candidate",
                str(candidate_path),
                "--candidate-id",
                candidate_id,
                "--run-state",
                str(run_state_path),
                "--apply",
            ],
        )
        artifact = {
            "title": event.get("title"),
            "path": event.get("path"),
            "produced_by": event.get("produced_by"),
        }
        self._emit_artifact_decision(
            run_state_path=run_state_path,
            artifact_id=str(event["artifact_id"]),
            artifact_status="approved",
            artifact=artifact,
            note=payload.decision_note or f"{event.get('title') or event['artifact_id']} 已在网页确认。",
        )
        self._advance_after_confirmed_gateway_candidate(
            run_state_path=run_state_path,
            artifact_id=str(event["artifact_id"]),
        )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)

    def revise_gateway_candidate(
        self,
        run_id: str,
        candidate_id: str,
        payload: DevShelfGatewayCandidateReviseRequest,
    ) -> DevShelfRunDetail:
        if not CANDIDATE_ID_RE.fullmatch(candidate_id):
            raise DevShelfGatewayConflict(f"Invalid Gateway candidate id: {candidate_id}")
        feedback = payload.feedback.strip()
        if not feedback:
            raise DevShelfGatewayConflict("修改意见不能为空。")
        if self._active_gateway_launch(run_id) is not None:
            raise DevShelfGatewayConflict("pi-agent 正在运行，请先中止后再提交修改意见。")

        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, payload.session_id)
        status = self._gateway_status_from_session(session_dir)
        if status.status != "completed":
            raise DevShelfGatewayConflict("pi-agent 未成功完成，不能基于本轮候选产物提交修改意见。")
        candidate_path = self._gateway_candidate_path(session_dir)
        candidate = self._gateway_candidate_from_file(candidate_path, candidate_id)
        event = candidate.get("event") if isinstance(candidate.get("event"), dict) else None
        if not event or not event.get("artifact_id"):
            raise DevShelfGatewayConflict(f"Gateway candidate is not revisable: {candidate_id}")

        run_state_path = run_dir / "run-state.json"
        self._run_dev_shelf_tool(
            "dev_shelf_workflow_action.py",
            [
                "--pretty",
                "apply-candidate",
                "--candidate",
                str(candidate_path),
                "--candidate-id",
                candidate_id,
                "--run-state",
                str(run_state_path),
                "--apply",
            ],
        )
        artifact = {
            "title": event.get("title"),
            "path": event.get("path"),
            "produced_by": event.get("produced_by"),
        }
        feedback_path = self._write_artifact_feedback(
            run_dir=run_dir,
            artifact_id=str(event["artifact_id"]),
            candidate_id=candidate_id,
            feedback=feedback,
        )
        self._emit_artifact_decision(
            run_state_path=run_state_path,
            artifact_id=str(event["artifact_id"]),
            artifact_status="rejected",
            artifact=artifact,
            note=f"用户要求修订并重新生成。反馈记录：{feedback_path}。修改意见：{feedback}",
            feedback_path=str(feedback_path),
        )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)

    def decide_human_gate(
        self,
        run_id: str,
        gate_id: str,
        payload: DevShelfHumanGateDecisionRequest,
    ) -> DevShelfRunDetail:
        if not GATE_ID_RE.fullmatch(gate_id):
            raise DevShelfGateConflict(f"Invalid gate id: {gate_id}")

        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        pending_gates = self._pending_human_gates(state)
        gate = next((item for item in pending_gates if item.gate_id == gate_id), None)
        if gate is None:
            known_gate = self._find_gate(state, gate_id)
            if known_gate is None:
                raise DevShelfGateConflict(f"Human gate not found or not pending: {gate_id}")
            raise DevShelfGateConflict(f"Human gate is not pending: {gate_id}")

        artifact_id = gate.artifact_id or self._artifact_id_for_gate(state, gate_id)
        run_state_path = run_dir / "run-state.json"
        decision_note = payload.decision_note or self._default_decision_note(gate_id, payload.decision)

        if artifact_id:
            artifact = self._find_artifact(state, artifact_id) or {}
            self._emit_artifact_decision(
                run_state_path=run_state_path,
                artifact_id=artifact_id,
                artifact_status=payload.decision,
                artifact=artifact,
                note=decision_note,
            )
            if payload.decision == "rejected":
                refreshed_state = self._load_state(run_dir, run_id)
                refreshed_gate = self._find_gate(refreshed_state, gate_id) or {}
                self._emit_gate_decision(
                    run_state_path=run_state_path,
                    gate_id=gate_id,
                    gate_status="rejected",
                    gate=refreshed_gate or gate.model_dump(),
                    note=decision_note,
                )
        else:
            self._emit_gate_decision(
                run_state_path=run_state_path,
                gate_id=gate_id,
                gate_status=payload.decision,
                gate=gate.model_dump(),
                note=decision_note,
            )

        self._write_next_packet(run_state_path)
        return self.get_run(run_id)

    def revise_artifact(
        self,
        run_id: str,
        artifact_id: str,
        payload: DevShelfArtifactReviseRequest,
    ) -> DevShelfRunDetail:
        if not GATE_ID_RE.fullmatch(artifact_id):
            raise DevShelfGateConflict(f"Invalid artifact id: {artifact_id}")
        feedback = payload.feedback.strip()
        if not feedback:
            raise DevShelfGateConflict("修改意见不能为空。")
        if self._active_gateway_launch(run_id) is not None:
            raise DevShelfGatewayConflict("pi-agent 正在运行，请先中止后再提交修改意见。")

        run_dir = self._run_dir(run_id)
        state = self._load_state(run_dir, run_id)
        artifact = self._find_artifact(state, artifact_id)
        if artifact is None:
            raise DevShelfGateConflict(f"Artifact not found: {artifact_id}")

        pending_gates = self._pending_human_gates(state)
        gate = next((item for item in pending_gates if item.artifact_id == artifact_id), None)
        if gate is None and artifact.get("status") not in {"draft", "in_review"}:
            raise DevShelfGateConflict(f"Artifact is not waiting for revision: {artifact_id}")

        run_state_path = run_dir / "run-state.json"
        feedback_path = self._write_artifact_feedback(
            run_dir=run_dir,
            artifact_id=artifact_id,
            candidate_id=None,
            feedback=feedback,
        )
        decision_note = f"用户要求修订并重新生成。反馈记录：{feedback_path}。修改意见：{feedback}"
        self._emit_artifact_decision(
            run_state_path=run_state_path,
            artifact_id=artifact_id,
            artifact_status="rejected",
            artifact=artifact,
            note=decision_note,
            feedback_path=str(feedback_path),
        )
        if gate is not None:
            refreshed_state = self._load_state(run_dir, run_id)
            refreshed_gate = self._find_gate(refreshed_state, gate.gate_id) or gate.model_dump()
            self._emit_gate_decision(
                run_state_path=run_state_path,
                gate_id=gate.gate_id,
                gate_status="rejected",
                gate=refreshed_gate,
                note=decision_note,
            )
        self._write_next_packet(run_state_path)
        return self.get_run(run_id)

    def _resolve_project_directory(self, raw_path: str | None, *, must_exist: bool) -> Path:
        raw_value = (raw_path or "").strip()
        candidate = Path(raw_value).expanduser() if raw_value else self.projects_root
        if not candidate.is_absolute():
            candidate = self.projects_root / candidate
        try:
            resolved = candidate.resolve(strict=must_exist)
        except OSError as exc:
            raise DevShelfProjectConflict(f"目录不存在或不可访问：{candidate}") from exc
        if not self._is_under_projects_root(resolved):
            raise DevShelfProjectConflict(f"目录不在允许的项目根目录内：{candidate}")
        return resolved

    def _is_under_projects_root(self, path: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(self.projects_root)
        except (OSError, ValueError):
            return False
        return True

    def _validate_new_directory_name(self, raw_name: str) -> str:
        name = raw_name.strip()
        if not name or name in {".", ".."} or not PROJECT_DIRECTORY_NAME_RE.fullmatch(name):
            raise DevShelfProjectConflict("目录名不能为空，且不能包含路径分隔符。")
        return name

    def _summary_from_state(self, state: dict[str, Any]) -> DevShelfRunSummary:
        artifacts = state.get("artifacts", [])
        return DevShelfRunSummary(
            run_id=str(state.get("run_id") or ""),
            project_name=state.get("project_name"),
            request_summary=state.get("request_summary"),
            current_stage=state.get("current_stage"),
            status=state.get("status"),
            task_type=state.get("task_type"),
            updated_at=self._updated_at(state),
            artifact_count=len(artifacts) if isinstance(artifacts, list) else 0,
        )

    def _artifact_from_state_item(self, run_dir: Path, item: dict[str, Any]) -> DevShelfArtifact:
        content, content_format, truncated, error = self._preview_artifact_content(
            run_dir,
            item.get("path"),
        )
        return DevShelfArtifact(
            artifact_id=str(item.get("artifact_id") or ""),
            title=str(item.get("title") or item.get("artifact_id") or ""),
            status=str(item.get("status") or "missing"),
            path=item.get("path"),
            produced_by=item.get("produced_by"),
            updated_at=item.get("updated_at"),
            content=content,
            content_format=content_format,
            content_truncated=truncated,
            content_error=error,
        )

    def _preview_artifact_content(
        self,
        run_dir: Path,
        raw_path: Any,
    ) -> tuple[str | None, str | None, bool, str | None]:
        if not raw_path:
            return None, None, False, None

        path, error = self._resolve_artifact_path(run_dir, str(raw_path))
        if error or path is None:
            return None, "unsupported", False, error

        content_format = self._content_format(path)
        if not path.is_file():
            return None, content_format, False, "产物文件不存在或不是普通文件。"

        try:
            raw = path.read_bytes()
        except OSError:
            return None, content_format, False, "产物文件读取失败。"

        truncated = len(raw) > ARTIFACT_PREVIEW_LIMIT
        raw = raw[:ARTIFACT_PREVIEW_LIMIT]
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, "unsupported", False, "当前产物不是 UTF-8 文本，暂不支持预览。"

        return content, content_format, truncated, None

    def _resolve_artifact_path(self, run_dir: Path, raw_path: str) -> tuple[Path | None, str | None]:
        del run_dir
        source = Path(raw_path).expanduser()
        candidate = source if source.is_absolute() else self.root / source
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return None, "产物路径不在 dev-shelf 根目录内，已拒绝预览。"
        return resolved, None

    def _content_format(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix == ".json":
            return "json"
        return "text"

    def _latest_packet(self, run_dir: Path) -> DevShelfExecutionPacket | None:
        packets_dir = run_dir / "packets"
        if not packets_dir.is_dir():
            return None

        candidates: list[tuple[int, Path]] = []
        for path in packets_dir.glob("*-execution-packet.json"):
            match = PACKET_RE.fullmatch(path.name)
            if match:
                candidates.append((int(match.group("sequence")), path))
        if not candidates:
            return None

        sequence, packet_path = sorted(candidates)[-1]
        content = self._load_json(packet_path)
        if not isinstance(content, dict):
            return None

        markdown_path = packet_path.with_suffix(".md")
        markdown = self._read_text(markdown_path) if markdown_path.exists() else None
        return DevShelfExecutionPacket(
            sequence=sequence,
            path=str(packet_path),
            markdown_path=str(markdown_path) if markdown_path.exists() else None,
            decision_type=content.get("decision_type"),
            target=content.get("target"),
            ready=content.get("ready"),
            content=content,
            markdown=markdown,
        )

    def _run_dir(self, run_id: str) -> Path:
        if not RUN_ID_RE.fullmatch(run_id):
            raise DevShelfRunNotFound(f"Invalid run id: {run_id}")

        run_dir = (self.runs_dir / run_id).resolve()
        if run_dir.parent != self.runs_dir.resolve() or not run_dir.is_dir():
            raise DevShelfRunNotFound(f"Run not found: {run_id}")
        return run_dir

    def _gateway_root(self, run_dir: Path) -> Path:
        return run_dir / "artifacts" / "pi-agent-gateway"

    def _gateway_session_dirs(self, run_dir: Path) -> list[Path]:
        gateway_root = self._gateway_root(run_dir)
        if not gateway_root.is_dir():
            return []
        return sorted(
            [
                path
                for path in gateway_root.iterdir()
                if path.is_dir() and (path / "session-metadata.json").is_file()
            ],
            key=lambda path: path.name,
        )

    def _gateway_session_sort_key(self, session_dir: Path) -> tuple[str, float, str]:
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        timestamp = str(metadata.get("finished_at") or metadata.get("started_at") or "")
        try:
            mtime = (session_dir / "session-metadata.json").stat().st_mtime
        except OSError:
            mtime = 0.0
        return timestamp, mtime, session_dir.name

    def _gateway_session_dir(self, run_dir: Path, session_id: str | None) -> Path:
        gateway_root = self._gateway_root(run_dir)
        if session_id:
            if not SESSION_ID_RE.fullmatch(session_id):
                raise DevShelfRunNotFound(f"Invalid Gateway session id: {session_id}")
            session_dir = (gateway_root / session_id).resolve()
            if session_dir.parent != gateway_root.resolve() or not (session_dir / "session-metadata.json").is_file():
                raise DevShelfRunNotFound(f"Gateway session not found: {session_id}")
            return session_dir

        sessions = self._gateway_session_dirs(run_dir)
        if not sessions:
            raise DevShelfRunNotFound(f"Gateway session not found: {run_dir.name}")
        return max(sessions, key=self._gateway_session_sort_key)

    def _gateway_path_or_none(self, path: Path) -> str | None:
        return str(path) if path.exists() else None

    def _gateway_status_from_session(self, session_dir: Path) -> DevShelfGatewaySessionStatus:
        metadata_path = session_dir / "session-metadata.json"
        metadata = self._load_json(metadata_path)
        if not isinstance(metadata, dict):
            raise DevShelfRunNotFound(f"Gateway session metadata not found: {session_dir.name}")

        runtime_events_path = session_dir / "runtime-events.jsonl"
        gateway_result_json = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_result_json"),
            default_name="gateway-result.json",
        )
        gateway_result_markdown = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_result_markdown"),
            default_name="gateway-result.md",
        )
        candidates_json = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_event_candidates_json"),
            default_name="gateway-event-candidates.json",
        )
        candidates_markdown = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_event_candidates_markdown"),
            default_name="gateway-event-candidates.md",
        )
        event_count = metadata.get("event_count")
        if not isinstance(event_count, int):
            event_count = self._count_jsonl_lines(runtime_events_path)

        return DevShelfGatewaySessionStatus(
            run_id=metadata.get("run_id"),
            gateway_session_id=metadata.get("gateway_session_id") or session_dir.name,
            status=metadata.get("status"),
            started_at=metadata.get("started_at"),
            finished_at=metadata.get("finished_at"),
            provider=metadata.get("provider"),
            model=metadata.get("model"),
            thinking=metadata.get("thinking"),
            pi_account=metadata.get("pi_account"),
            pi_account_dir=metadata.get("pi_account_dir"),
            pi_session_id=metadata.get("pi_session_id"),
            packet_path=metadata.get("packet_path"),
            packet_target=metadata.get("packet_target"),
            session_dir=str(session_dir),
            metadata_path=str(metadata_path),
            runtime_events_path=self._gateway_path_or_none(runtime_events_path),
            runtime_event_schema_version=metadata.get("runtime_event_schema_version"),
            event_count=event_count,
            gateway_result_json=self._gateway_path_or_none(gateway_result_json),
            gateway_result_markdown=self._gateway_path_or_none(gateway_result_markdown),
            gateway_event_candidates_json=self._gateway_path_or_none(candidates_json),
            gateway_event_candidates_markdown=self._gateway_path_or_none(candidates_markdown),
            artifact_result_summary=metadata.get("artifact_result_summary"),
            event_candidate_summary=metadata.get("event_candidate_summary"),
            abort_requested=bool(metadata.get("abort_requested", False)),
            error=metadata.get("error"),
        )

    def _gateway_launch_status(self, run_dir: Path, run_id: str) -> DevShelfGatewaySessionStatus | None:
        launch = self._gateway_launches.get(run_id)
        if launch is None:
            return None

        latest_status = self._latest_gateway_session_status(run_dir)
        if latest_status is not None and self._gateway_session_matches_launch(latest_status, launch):
            launch.gateway_session_id = latest_status.gateway_session_id
            if latest_status.status in {"completed", "failed"} and launch.process.poll() is not None:
                self._gateway_launches.pop(run_id, None)
            return latest_status

        returncode = launch.process.poll()
        if returncode is None:
            return DevShelfGatewaySessionStatus(
                run_id=run_id,
                status="starting",
                started_at=launch.started_at,
                event_count=0,
            )

        self._gateway_launches.pop(run_id, None)
        detail = self._read_log_tail(launch.log_path) or f"Gateway exited with code {returncode}"
        return DevShelfGatewaySessionStatus(
            run_id=run_id,
            status="failed",
            started_at=launch.started_at,
            finished_at=self._utc_now(),
            event_count=0,
            error=detail,
        )

    def _latest_gateway_session_status(self, run_dir: Path) -> DevShelfGatewaySessionStatus | None:
        sessions = self._gateway_session_dirs(run_dir)
        if not sessions:
            return None
        return self._gateway_status_from_session(max(sessions, key=self._gateway_session_sort_key))

    def _gateway_session_matches_launch(
        self,
        status: DevShelfGatewaySessionStatus,
        launch: DevShelfGatewayLaunch,
    ) -> bool:
        session_started_at = self._parse_utc(status.started_at)
        launch_started_at = self._parse_utc(launch.started_at)
        if session_started_at is None or launch_started_at is None:
            return False
        return session_started_at >= launch_started_at

    def _gateway_metadata_path(self, session_dir: Path, raw_path: Any, *, default_name: str) -> Path:
        candidate = Path(str(raw_path)) if raw_path else session_dir / default_name
        if not candidate.is_absolute():
            candidate = session_dir / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(session_dir.resolve())
        except (OSError, ValueError) as exc:
            raise DevShelfRunNotFound(f"Gateway artifact path is outside session dir: {candidate}") from exc
        return resolved

    def _count_jsonl_lines(self, path: Path) -> int:
        if not path.is_file():
            return 0
        try:
            with path.open("r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0

    def _normalize_gateway_cursor(self, cursor: int | str | None) -> int:
        if cursor is None or cursor == "":
            return 0
        try:
            value = int(cursor)
        except (TypeError, ValueError) as exc:
            raise DevShelfRunNotFound(f"Invalid runtime event cursor: {cursor}") from exc
        if value < 0:
            raise DevShelfRunNotFound("Runtime event cursor must be >= 0")
        return value

    def _normalize_gateway_limit(self, limit: int | str | None) -> int:
        if limit is None or limit == "":
            return GATEWAY_EVENT_LIMIT_DEFAULT
        try:
            value = int(limit)
        except (TypeError, ValueError) as exc:
            raise DevShelfRunNotFound(f"Invalid runtime event limit: {limit}") from exc
        if value <= 0:
            raise DevShelfRunNotFound("Runtime event limit must be > 0")
        return min(value, GATEWAY_EVENT_LIMIT_MAX)

    def _gateway_stream_start_cursor(self, cursor: int | str | None, last_event_id: str | None) -> int:
        if last_event_id:
            parsed = self._cursor_from_sse_event_id(last_event_id)
            if parsed is not None:
                return parsed
        return self._normalize_gateway_cursor(cursor)

    def _read_gateway_runtime_events(
        self,
        runtime_events_path: Path,
        *,
        cursor: int | str | None,
        limit: int | str | None,
    ) -> dict[str, Any]:
        start_after = self._normalize_gateway_cursor(cursor)
        actual_limit = self._normalize_gateway_limit(limit)
        events: list[dict[str, Any]] = []
        has_more = False
        next_cursor = start_after
        total_seen = 0

        with runtime_events_path.open("r", encoding="utf-8") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                total_seen += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DevShelfRunNotFound(f"Invalid Gateway runtime event at line {line_number}") from exc
                if not isinstance(event, dict):
                    raise DevShelfRunNotFound(f"Gateway runtime event at line {line_number} is not an object")
                sequence = event.get("sequence")
                if not isinstance(sequence, int):
                    sequence = line_number
                    event = {**event, "sequence": sequence}
                if sequence <= start_after:
                    continue
                if len(events) >= actual_limit:
                    has_more = True
                    break
                events.append(event)
                next_cursor = max(next_cursor, sequence)

        return {
            "cursor": start_after,
            "next_cursor": next_cursor,
            "limit": actual_limit,
            "has_more": has_more,
            "event_count": len(events),
            "total_seen": total_seen,
            "events": events,
        }

    def _normalize_gateway_stream_page(self, page: dict[str, Any]) -> dict[str, Any]:
        normalizer = self._load_dev_shelf_stream_normalizer()
        if normalizer is not None:
            return normalizer(page)
        return self._fallback_normalize_gateway_stream_page(page)

    def _load_dev_shelf_stream_normalizer(self):
        inserted = False
        tools_root = str(self.tools_root)
        if tools_root not in sys.path:
            sys.path.insert(0, tools_root)
            inserted = True
        try:
            from dev_shelf_gateway.workbench_stream_event import normalize_runtime_events_page

            return normalize_runtime_events_page
        except (ImportError, ModuleNotFoundError):
            return None
        finally:
            if inserted:
                try:
                    sys.path.remove(tools_root)
                except ValueError:
                    pass

    def _fallback_normalize_gateway_stream_page(self, page: dict[str, Any]) -> dict[str, Any]:
        runtime_events = page.get("events")
        if not isinstance(runtime_events, list):
            runtime_events = []
        events: list[dict[str, Any]] = []
        for runtime_event in runtime_events:
            if isinstance(runtime_event, dict):
                events.extend(self._fallback_normalize_gateway_stream_event(runtime_event))
        return {
            "schema_version": "1.0",
            "workbench_stream_event_schema_version": "1.0",
            "run_id": page.get("run_id"),
            "gateway_session_id": page.get("gateway_session_id"),
            "session_dir": page.get("session_dir"),
            "runtime_events_path": page.get("runtime_events_path"),
            "cursor": page.get("cursor"),
            "next_cursor": page.get("next_cursor"),
            "limit": page.get("limit"),
            "has_more": page.get("has_more"),
            "runtime_event_count": page.get("event_count"),
            "event_count": len(events),
            "events": events,
        }

    def _fallback_normalize_gateway_stream_event(self, runtime_event: dict[str, Any]) -> list[dict[str, Any]]:
        raw = runtime_event.get("raw") if isinstance(runtime_event.get("raw"), dict) else {}
        raw_type = str(raw.get("type") or raw.get("event") or raw.get("kind") or "")
        events: list[dict[str, Any]] = []

        def add(event_type: str, payload: dict[str, Any]) -> None:
            events.append(self._gateway_stream_event(runtime_event, raw, event_type, payload, len(events)))

        if runtime_event.get("kind") == "stderr" and isinstance(raw.get("line"), str):
            add("error", {"message": raw["line"], "source": "stderr"})
            return events

        if raw_type == "response":
            command = raw.get("command") if isinstance(raw.get("command"), str) else None
            if raw.get("success") is False:
                add(
                    "error",
                    {
                        "message": raw.get("error") or f"Gateway command failed: {command or 'unknown'}",
                        "command": command,
                        "source": "response",
                    },
                )
            elif command == "prompt":
                add("status", {"status": "prompt_accepted", "command": command})
            return events

        if raw_type in {"agent_start", "agent_end", "turn_start", "turn_end", "lifecycle_abort"}:
            status_by_type = {
                "agent_start": "started",
                "agent_end": "completed",
                "turn_start": "turn_started",
                "turn_end": "turn_completed",
                "lifecycle_abort": "aborted",
            }
            add("status", {"status": status_by_type[raw_type], "raw_status": raw_type})
            return events

        if raw_type in {"artifact_candidate", "gateway_event_candidate"}:
            add("artifact_candidate", self._fallback_gateway_artifact_candidate_payload(raw))
            return events

        delta = self._runtime_assistant_delta(runtime_event, raw)
        if delta:
            add("assistant_delta", {"delta": delta})

        if raw_type in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
            self._fallback_normalize_gateway_tool_event(raw, add)

        return events

    def _fallback_gateway_artifact_candidate_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
        source_output = raw.get("source_output") if isinstance(raw.get("source_output"), dict) else {}
        artifact_id = (
            self._string_value(raw.get("artifact_id"))
            or self._string_value(event.get("artifact_id"))
            or self._string_value(source_output.get("artifact_id"))
        )
        path = (
            self._string_value(raw.get("path"))
            or self._string_value(event.get("path"))
            or self._first_declared_output_path(source_output)
        )
        status = (
            self._string_value(raw.get("status") or raw.get("artifact_status"))
            or self._string_value(event.get("artifact_status") or event.get("status"))
            or self._string_value(source_output.get("status_on_produce"))
        )
        title = (
            self._string_value(raw.get("title"))
            or self._string_value(event.get("title"))
            or self._string_value(source_output.get("title"))
        )
        return {
            "artifact_id": artifact_id or None,
            "title": title or None,
            "path": path or None,
            "status": status or None,
            "candidate_id": self._string_value(raw.get("candidate_id")) or None,
            "produced_by": self._string_value(raw.get("produced_by") or event.get("produced_by")) or None,
            "review_required": bool(raw.get("review_required") or source_output.get("review_required")),
        }

    def _string_value(self, value: Any) -> str:
        return value if isinstance(value, str) else ""

    def _fallback_normalize_gateway_tool_event(self, raw: dict[str, Any], add) -> None:
        raw_type = str(raw.get("type") or "")
        tool_name = raw.get("toolName") if isinstance(raw.get("toolName"), str) else None
        tool_call_id = raw.get("toolCallId") if isinstance(raw.get("toolCallId"), str) else None
        if raw_type == "tool_execution_start":
            args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
            add(
                "tool_call",
                {
                    "phase": "started",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": self._summarize_gateway_tool_args(args),
                },
            )
            return
        result_key = "partialResult" if raw_type == "tool_execution_update" else "result"
        result = raw.get(result_key) if isinstance(raw.get(result_key), dict) else {}
        text = self._gateway_content_text(result.get("content"))
        add(
            "tool_result",
            {
                "phase": "updated" if raw_type == "tool_execution_update" else "completed",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "is_error": bool(raw.get("isError")),
                "text": text,
                "text_truncated": False,
            },
        )
        match = re.match(r"^Successfully wrote (?P<bytes>\d+) bytes to (?P<path>.+)$", text.strip())
        if raw_type == "tool_execution_end" and tool_name in {"write", "edit"} and match and not raw.get("isError"):
            add(
                "file_write",
                {
                    "path": match.group("path").strip(),
                    "bytes": int(match.group("bytes")),
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                },
            )

    def _gateway_stream_event(
        self,
        runtime_event: dict[str, Any],
        raw: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
        index: int,
    ) -> dict[str, Any]:
        sequence = runtime_event.get("sequence")
        gateway_session_id = runtime_event.get("gateway_session_id")
        return {
            "schema_version": "1.0",
            "event_id": f"{gateway_session_id or 'session'}:{sequence or 0}:{event_type}:{index}",
            "event_type": event_type,
            "cursor": sequence,
            "runtime_sequence": sequence,
            "ts": runtime_event.get("ts"),
            "run_id": runtime_event.get("run_id"),
            "gateway_session_id": gateway_session_id,
            "pi_session_id": runtime_event.get("pi_session_id"),
            "payload": payload,
            "source": {
                "stream": runtime_event.get("stream"),
                "kind": runtime_event.get("kind"),
                "raw_type": raw.get("type"),
            },
        }

    def _read_gateway_transcript(self, runtime_events_path: Path) -> tuple[list[DevShelfGatewayTranscriptMessage], int]:
        messages: list[DevShelfGatewayTranscriptMessage] = []
        assistant_text = ""
        assistant_start: int | None = None
        assistant_end: int | None = None
        assistant_ts: str | None = None
        seen_tool_messages: set[str] = set()
        event_count = 0

        def flush_assistant() -> None:
            nonlocal assistant_text, assistant_start, assistant_end, assistant_ts
            text = assistant_text.strip()
            if text:
                messages.append(
                    DevShelfGatewayTranscriptMessage(
                        role="assistant",
                        kind="message",
                        text=text,
                        sequence_start=assistant_start,
                        sequence_end=assistant_end,
                        ts=assistant_ts,
                    )
                )
            assistant_text = ""
            assistant_start = None
            assistant_end = None
            assistant_ts = None

        with runtime_events_path.open("r", encoding="utf-8") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                event_count += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DevShelfRunNotFound(f"Invalid Gateway runtime event at line {line_number}") from exc
                if not isinstance(event, dict):
                    raise DevShelfRunNotFound(f"Gateway runtime event at line {line_number} is not an object")

                sequence = event.get("sequence")
                if not isinstance(sequence, int):
                    sequence = line_number
                ts = event.get("ts") if isinstance(event.get("ts"), str) else None
                raw = event.get("raw") if isinstance(event.get("raw"), dict) else {}

                snapshot = self._runtime_assistant_snapshot(raw)
                delta = self._runtime_assistant_delta(event, raw)
                if snapshot is not None or delta:
                    if assistant_start is None:
                        assistant_start = sequence
                        assistant_ts = ts
                    assistant_end = sequence
                    if snapshot is not None:
                        assistant_text = snapshot
                    else:
                        assistant_text += delta
                    continue

                tool_text = self._runtime_tool_summary(raw)
                if tool_text:
                    flush_assistant()
                    key = f"{event.get('kind')}:{tool_text}"
                    if key not in seen_tool_messages:
                        seen_tool_messages.add(key)
                        messages.append(
                            DevShelfGatewayTranscriptMessage(
                                role="tool",
                                kind="tool",
                                text=tool_text,
                                sequence_start=sequence,
                                sequence_end=sequence,
                                ts=ts,
                            )
                        )
                    continue

                status_text = self._runtime_status_summary(raw)
                if status_text:
                    flush_assistant()
                    messages.append(
                        DevShelfGatewayTranscriptMessage(
                            role="system",
                            kind="status",
                            text=status_text,
                            sequence_start=sequence,
                            sequence_end=sequence,
                            ts=ts,
                        )
                    )
                    continue

                if event.get("kind") == "stderr" and isinstance(raw.get("line"), str):
                    flush_assistant()
                    messages.append(
                        DevShelfGatewayTranscriptMessage(
                            role="error",
                            kind="stderr",
                            text=raw["line"],
                            sequence_start=sequence,
                            sequence_end=sequence,
                            ts=ts,
                        )
                    )

        flush_assistant()
        return messages[-200:], event_count

    def _runtime_assistant_delta(self, event: dict[str, Any], raw: dict[str, Any]) -> str:
        if event.get("kind") == "text" and isinstance(raw.get("delta"), str):
            return raw["delta"]
        assistant_event = raw.get("assistantMessageEvent")
        if isinstance(assistant_event, dict) and assistant_event.get("type") == "text_delta":
            partial = assistant_event.get("partial")
            if isinstance(partial, dict) and partial.get("role") not in {None, "assistant"}:
                return ""
            delta = assistant_event.get("delta")
            return delta if isinstance(delta, str) else ""
        return ""

    def _runtime_assistant_snapshot(self, raw: dict[str, Any]) -> str | None:
        message = raw.get("message")
        assistant_event = raw.get("assistantMessageEvent")
        if not isinstance(message, dict) and isinstance(assistant_event, dict):
            message = assistant_event.get("partial")
        if not isinstance(message, dict):
            return None
        if message.get("role") not in {None, "assistant"}:
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        return "\n\n".join(text_parts) if text_parts else None

    def _gateway_content_text(self, content: Any) -> str:
        if not isinstance(content, list):
            return ""
        return "\n\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )

    def _summarize_gateway_tool_args(self, args: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in args.items():
            if key in {"content", "input"} and isinstance(value, str):
                summary[f"{key}_char_count"] = len(value)
            elif isinstance(value, str):
                summary[key] = value[:300]
            elif isinstance(value, (int, float, bool)) or value is None:
                summary[key] = value
            elif isinstance(value, list):
                summary[key] = {"item_count": len(value)}
            elif isinstance(value, dict):
                summary[key] = {"keys": sorted(str(item_key) for item_key in value.keys())}
        return summary

    def _cursor_from_sse_event_id(self, event_id: str) -> int | None:
        raw = event_id.strip()
        if not raw:
            return None
        if raw.isdigit():
            return int(raw)
        for part in raw.split(":"):
            if part.isdigit():
                return int(part)
        return None

    def _sse_encode_event(self, event: dict[str, Any]) -> str:
        event_id = str(event.get("event_id") or event.get("cursor") or "")
        event_type = str(event.get("event_type") or "message")
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event_type}")
        lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
        lines.append("")
        lines.append("")
        return "\n".join(lines)

    def _runtime_tool_summary(self, raw: dict[str, Any]) -> str | None:
        if raw.get("command"):
            success = raw.get("success")
            suffix = "失败" if success is False else "完成" if success is True else ""
            return f"{raw.get('command')}{suffix}"

        message = raw.get("message")
        assistant_event = raw.get("assistantMessageEvent")
        if not isinstance(message, dict) and isinstance(assistant_event, dict):
            message = assistant_event.get("partial")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return None
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "toolCall":
                name = item.get("name") or item.get("toolName") or "tool"
                return f"工具调用：{name}"
            if item.get("type") == "toolResult":
                name = item.get("toolName") or item.get("name") or "tool"
                is_error = item.get("isError") is True
                return f"工具结果：{name}{' 失败' if is_error else ''}"
        return None

    def _runtime_status_summary(self, raw: dict[str, Any]) -> str | None:
        event_type = raw.get("type")
        if event_type == "agent_start":
            return "Agent 已启动"
        if event_type == "turn_start":
            return "开始处理本轮任务"
        if event_type == "turn_end":
            return "本轮任务处理完成"
        if event_type == "lifecycle_abort":
            return "运行已中止"
        return None

    def _gateway_artifact_payload(
        self,
        run_id: str,
        session_id: str | None,
        *,
        metadata_field: str,
        default_name: str,
        preview_candidates: bool = False,
    ) -> DevShelfGatewayArtifactPayload:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        path = self._gateway_metadata_path(session_dir, metadata.get(metadata_field), default_name=default_name)
        payload = self._load_json(path) if path.is_file() else None
        status = self._gateway_status_from_session(session_dir)
        if preview_candidates and isinstance(payload, dict):
            if status.status == "completed":
                payload = self._with_candidate_artifact_previews(run_dir, payload)
            else:
                payload = {
                    **payload,
                    "candidates": [],
                    "preview_artifacts": [],
                    "summary": {
                        **(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}),
                        "candidate_count": 0,
                        "review_required_candidate_count": 0,
                    },
                    "skipped_reason": f"gateway_status_{status.status}",
                }
        return DevShelfGatewayArtifactPayload(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            path=str(path) if path.exists() else None,
            payload=payload,
        )

    def _gateway_candidate_path(self, session_dir: Path) -> Path:
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        path = self._gateway_metadata_path(
            session_dir,
            metadata.get("gateway_event_candidates_json"),
            default_name="gateway-event-candidates.json",
        )
        if not path.is_file():
            raise DevShelfRunNotFound(f"Gateway candidate file not found: {path}")
        return path

    def _gateway_candidate_from_file(self, candidate_path: Path, candidate_id: str) -> dict[str, Any]:
        payload = self._load_json(candidate_path)
        if not isinstance(payload, dict):
            raise DevShelfGatewayConflict("Gateway candidate file is invalid.")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise DevShelfGatewayConflict("Gateway candidate file has no candidates.")
        for item in candidates:
            if isinstance(item, dict) and item.get("candidate_id") == candidate_id:
                return item
        raise DevShelfRunNotFound(f"Gateway candidate not found: {candidate_id}")

    def _with_candidate_artifact_previews(self, run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        generated_at = payload.get("generated_at")
        previews: list[dict[str, Any]] = []
        for candidate in payload.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            preview = self._candidate_artifact_preview(run_dir, candidate, generated_at=generated_at)
            if preview is not None:
                previews.append(preview)
        enriched["preview_artifacts"] = previews
        return enriched

    def _candidate_artifact_preview(
        self,
        run_dir: Path,
        candidate: dict[str, Any],
        *,
        generated_at: Any,
    ) -> dict[str, Any] | None:
        event = candidate.get("event") if isinstance(candidate.get("event"), dict) else {}
        source_output = candidate.get("source_output") if isinstance(candidate.get("source_output"), dict) else {}
        artifact_id = (
            candidate.get("artifact_id")
            or event.get("artifact_id")
            or source_output.get("artifact_id")
            or candidate.get("candidate_id")
        )
        if not artifact_id:
            return None

        raw_path = event.get("path") or self._first_declared_output_path(source_output)
        content, content_format, truncated, error = self._preview_artifact_content(run_dir, raw_path)
        status = event.get("artifact_status") or source_output.get("status_on_produce") or "draft"
        return {
            "artifact_id": str(artifact_id),
            "title": str(event.get("title") or source_output.get("title") or artifact_id),
            "status": str(status),
            "path": str(raw_path) if raw_path else None,
            "produced_by": event.get("produced_by"),
            "updated_at": generated_at if isinstance(generated_at, str) else None,
            "content": content,
            "content_format": content_format,
            "content_truncated": truncated,
            "content_error": error,
            "review_required": bool(candidate.get("review_required") or source_output.get("review_required")),
            "candidate_id": candidate.get("candidate_id"),
            "source": "gateway_candidate",
        }

    def _first_declared_output_path(self, source_output: dict[str, Any]) -> str | None:
        declared_paths = source_output.get("declared_paths")
        if not isinstance(declared_paths, list):
            return None
        for item in declared_paths:
            if isinstance(item, dict) and item.get("path"):
                return str(item["path"])
        return None

    def _is_execution_gateway_result(
        self,
        status: DevShelfGatewaySessionStatus,
        latest_packet: DevShelfExecutionPacket | None,
    ) -> bool:
        if status.packet_target != "stage.execution":
            return False
        if latest_packet is None or not latest_packet.path or not status.packet_path:
            return False
        try:
            return Path(status.packet_path).resolve() == Path(latest_packet.path).resolve()
        except OSError:
            return False

    def _implementation_result_path(
        self,
        run_dir: Path,
        latest_packet: DevShelfExecutionPacket | None,
        status: DevShelfGatewaySessionStatus,
    ) -> Path | None:
        raw_path = self._implementation_result_path_from_packet(latest_packet.content if latest_packet else None)
        if raw_path is None and status.packet_path:
            packet_payload = self._load_json(Path(status.packet_path))
            raw_path = self._implementation_result_path_from_packet(packet_payload)
        if raw_path is None:
            raw_path = self._infer_implementation_result_path(run_dir)
        if raw_path is None:
            return None

        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise DevShelfGatewayConflict("implementation_result 路径必须位于 dev-shelf 根目录内。") from exc
        return resolved

    def _implementation_result_path_from_packet(self, content: dict[str, Any] | None) -> str | None:
        if not isinstance(content, dict):
            return None
        outputs = content.get("pending_outputs")
        if not isinstance(outputs, list):
            outputs = content.get("outputs_to_produce")
        if not isinstance(outputs, list):
            return None
        for item in outputs:
            if not isinstance(item, dict) or item.get("artifact_id") != "implementation_result":
                continue
            raw_path = item.get("path") or item.get("current_path")
            if raw_path:
                return str(raw_path)
            state_event = item.get("state_event_on_draft")
            if isinstance(state_event, dict) and state_event.get("path"):
                return str(state_event["path"])
        return None

    def _infer_implementation_result_path(self, run_dir: Path) -> str | None:
        state = self._load_json(run_dir / "run-state.json") or {}
        for artifact in state.get("artifacts", []):
            if not isinstance(artifact, dict) or not artifact.get("path"):
                continue
            path = Path(str(artifact["path"]))
            if not path.is_absolute():
                path = self.root / path
            try:
                resolved = path.resolve(strict=False)
                resolved.relative_to(self.root / "docs")
            except (OSError, ValueError):
                continue
            return str(resolved.parent / "implementation-result.md")
        return None

    def _render_gateway_implementation_result(
        self,
        *,
        run_id: str,
        status: DevShelfGatewaySessionStatus,
        messages: list[DevShelfGatewayTranscriptMessage],
        target_path: Path,
        note: str | None,
    ) -> str:
        written_files = self._written_files_from_transcript(messages)
        final_message = self._final_gateway_message_text(messages)
        model_label = " / ".join(item for item in [status.provider, status.model] if item)
        lines = [
            "# 实现结果",
            "",
            "## 登记信息",
            "",
            f"- run_id: `{run_id}`",
            f"- gateway_session_id: `{status.gateway_session_id or '-'}`",
            f"- packet: `{status.packet_path or '-'}`",
            f"- model: `{model_label or '-'}`",
        ]
        if status.finished_at:
            lines.append(f"- finished_at: `{status.finished_at}`")
        lines.append(f"- artifact_path: `{target_path}`")
        if note:
            lines.extend(["", "## 备注", "", note.strip()])

        if written_files:
            lines.extend(["", "## 修改文件", ""])
            for path in written_files:
                lines.append(f"- `{path}`")
        else:
            lines.extend(["", "## 修改文件", "", "- 未从运行对话中识别到写入文件。"])

        lines.extend(["", "## pi-agent 最终回复", ""])
        lines.append(final_message or "未捕获到最终回复。")
        return "\n".join(lines).rstrip() + "\n"

    def _written_files_from_transcript(self, messages: list[DevShelfGatewayTranscriptMessage]) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for message in messages:
            for match in re.finditer(r"Successfully wrote \d+ bytes to ([^\s]+)", message.text or ""):
                path = match.group(1)
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths

    def _final_gateway_message_text(self, messages: list[DevShelfGatewayTranscriptMessage]) -> str | None:
        for role in ["assistant", "tool", "system", "error"]:
            for message in reversed(messages):
                text = (message.text or "").strip()
                if message.role == role and text:
                    return text
        return None

    def _project_intake_from_payload(self, payload: DevShelfProjectCreateRequest) -> dict[str, Any]:
        project_name = payload.project_name.strip()
        requirement = payload.requirement.strip()
        if not project_name:
            raise DevShelfProjectConflict("项目名不能为空。")
        if not requirement:
            raise DevShelfProjectConflict("需求不能为空。")

        project_slug = self._normalize_project_slug(payload.project_slug or project_name)
        self._ensure_unique_project_slug(project_slug, project_name)
        request_summary = (payload.request_summary or "").strip() or self._summarize_requirement(requirement)
        intake: dict[str, Any] = {
            "schema_version": "1.0",
            "project_name": project_name,
            "project_slug": project_slug,
            "request_summary": request_summary,
            "requirement_draft": requirement,
            "task_type": payload.task_type,
            "task_type_status": payload.task_type_status,
            "project_context": payload.project_context,
            "requires_existing_project_analysis": payload.project_context == "existing_project",
            "mode": "standard",
        }

        workspace = self._workspace_from_payload(payload, project_slug=project_slug)
        if workspace is not None:
            intake["workspace"] = workspace
        return intake

    def _normalize_project_slug(self, raw_value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_value.strip().lower())
        slug = re.sub(r"_+", "_", slug).strip("_-")
        if not slug:
            slug = f"web_project_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        if not PROJECT_SLUG_RE.fullmatch(slug):
            raise DevShelfProjectConflict(f"Invalid project slug: {raw_value!r}")
        return slug

    def _ensure_unique_project_slug(self, project_slug: str, project_name: str) -> None:
        docs_dir = self.root / "docs" / project_slug
        if docs_dir.exists():
            raise DevShelfProjectConflict(
                f"项目名已存在：docs/{project_slug} 已被使用。请换一个项目名，避免新 run 读取旧产物。"
            )

        normalized_name = project_name.strip().lower()
        if not self.runs_dir.is_dir():
            return
        for run_state_path in self.runs_dir.glob("*/run-state.json"):
            state = self._load_json(run_state_path)
            if not isinstance(state, dict):
                continue
            existing_name = state.get("project_name")
            if not isinstance(existing_name, str):
                continue
            if existing_name.strip().lower() == normalized_name or self._normalize_project_slug(existing_name) == project_slug:
                run_id = state.get("run_id") or run_state_path.parent.name
                raise DevShelfProjectConflict(
                    f"项目名已被 run `{run_id}` 使用。请换一个项目名，避免不同 run 复用同一 docs 目录。"
                )

    def _summarize_requirement(self, requirement: str) -> str:
        first_line = next((line.strip() for line in requirement.splitlines() if line.strip()), "")
        if not first_line:
            return "网页创建的 dev-shelf 任务"
        return first_line[:120]

    def _workspace_from_payload(
        self,
        payload: DevShelfProjectCreateRequest,
        *,
        project_slug: str,
    ) -> dict[str, Any] | None:
        raw_project_path = (payload.project_path or "").strip()
        if not raw_project_path and payload.project_context == "new_project":
            raw_project_path = str(self.root.parent / project_slug)
        if not raw_project_path:
            return None

        project_path = str(Path(raw_project_path).expanduser().resolve(strict=False))
        workspace: dict[str, Any] = {
            "kind": payload.project_context,
            "project_path": project_path,
            "allow_create_project_dir": payload.allow_create_project_dir,
            "allowed_read_paths": [project_path],
            "allowed_write_paths": [project_path] if payload.workspace_confirmed else [],
            "confirmation_status": "confirmed" if payload.workspace_confirmed else "unconfirmed",
        }
        if payload.project_context == "existing_project":
            workspace["existing_project_path"] = project_path
        if payload.project_context == "new_project":
            workspace["root_path"] = str(Path(project_path).parent)
        if payload.workspace_confirmed:
            workspace["confirmed_by"] = "human"
            workspace["confirmed_at"] = self._utc_now()
            workspace["note"] = "用户在网页创建 run 时确认 workspace。"
        return workspace

    def _ensure_gateway_runnable(self, run_dir: Path) -> None:
        latest_packet = self._latest_packet(run_dir)
        content = latest_packet.content if latest_packet else None
        if not isinstance(content, dict):
            raise DevShelfGatewayConflict("当前 run 没有可执行的 execution packet。")
        if content.get("decision_type") != "run_manifest":
            raise DevShelfGatewayConflict("当前下一步不是生成任务，请先继续流程或处理待确认事项。")

        outputs = content.get("pending_outputs")
        if not isinstance(outputs, list) or not outputs:
            outputs = content.get("outputs_to_produce")
        if not any(isinstance(item, dict) for item in (outputs or [])):
            raise DevShelfGatewayConflict("当前 packet 没有待生成产物，不应启动 pi-agent。")

        latest_status = self._latest_gateway_session_status(run_dir)
        if (
            latest_status is not None
            and latest_status.status == "completed"
            and latest_status.packet_path
            and latest_packet.path
            and self._same_path(latest_status.packet_path, latest_packet.path)
        ):
            raise DevShelfGatewayConflict("当前 execution packet 已完成执行，请等待流程生成下一份 packet 后再启动。")

        workspace = content.get("workspace") if isinstance(content.get("workspace"), dict) else {}
        runtime = (
            content.get("agent_runtime_contract")
            if isinstance(content.get("agent_runtime_contract"), dict)
            else {}
        )
        cwd = workspace.get("project_path") or runtime.get("cwd")
        if not cwd:
            raise DevShelfGatewayConflict(
                "当前 run 没有项目路径，不能启动 pi-agent。请填写项目路径后重新创建 run。"
            )

    def _same_path(self, left: str, right: str) -> bool:
        try:
            return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
        except OSError:
            return left == right

    def _active_gateway_launch(self, run_id: str) -> DevShelfGatewayLaunch | None:
        launch = self._gateway_launches.get(run_id)
        if launch is None:
            return None
        if launch.process.poll() is None:
            return launch
        self._gateway_launches.pop(run_id, None)
        return None

    def _gateway_start_command(self, run_id: str, payload: DevShelfGatewayStartRequest) -> list[str]:
        script_path = self.tools_root / "scripts" / "dev_shelf_gateway.py"
        if not script_path.is_file():
            raise DevShelfToolError(f"dev-shelf gateway script not found: {script_path}")

        account = self._normalized_gateway_account(payload.account)
        provider = self._normalized_gateway_value(
            payload.provider,
            PI_PROVIDER_RE,
            field_name="provider",
            default="openai-codex",
        )
        model = self._normalized_gateway_value(
            payload.model,
            PI_MODEL_RE,
            field_name="model",
            default="gpt-5.4",
        )
        thinking = self._normalized_gateway_value(
            payload.thinking,
            PI_MODEL_RE,
            field_name="thinking",
            default=None,
        )

        command = [
            sys.executable,
            str(script_path),
            "--run-id",
            run_id,
            "--dev-shelf-root",
            str(self.root),
            "--provider",
            provider,
            "--model",
            model,
            "--request-timeout-seconds",
            str(self._normalized_gateway_seconds(payload.request_timeout_seconds, "request_timeout_seconds")),
            "--poll-interval-seconds",
            str(self._normalized_gateway_seconds(payload.poll_interval_seconds, "poll_interval_seconds")),
            "--post-prompt-grace-seconds",
            str(self._normalized_gateway_seconds(payload.post_prompt_grace_seconds, "post_prompt_grace_seconds")),
            "--pretty",
        ]
        if account:
            command.extend(["--account", account])
        if thinking:
            command.extend(["--thinking", thinking])
        if payload.no_session:
            command.append("--no-session")
        if payload.light_mode:
            command.extend(["--pi-arg=--no-tools", "--pi-arg=--no-context-files"])
        return command

    def _normalized_gateway_account(self, account: str | None) -> str | None:
        if account is None:
            return None
        value = account.strip().lower()
        if not value:
            return None
        if not PI_ACCOUNT_RE.fullmatch(value):
            raise DevShelfGatewayConflict(f"Invalid Gateway account: {account!r}")
        return value

    def _normalized_gateway_value(
        self,
        value: str | None,
        pattern: re.Pattern[str],
        *,
        field_name: str,
        default: str | None,
    ) -> str | None:
        raw = value if value is not None else default
        if raw is None:
            return None
        normalized = raw.strip()
        if not normalized:
            if default is None:
                return None
            normalized = default
        if not pattern.fullmatch(normalized):
            raise DevShelfGatewayConflict(f"Invalid Gateway {field_name}: {raw!r}")
        return normalized

    def _normalized_gateway_seconds(self, value: float, field_name: str) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise DevShelfGatewayConflict(f"Invalid Gateway {field_name}: {value!r}") from exc
        if seconds <= 0 or seconds > GATEWAY_START_MAX_TIMEOUT_SECONDS:
            raise DevShelfGatewayConflict(
                f"Gateway {field_name} must be between 0 and {int(GATEWAY_START_MAX_TIMEOUT_SECONDS)} seconds"
            )
        return seconds

    def _spawn_gateway_process(
        self,
        command: list[str],
        log_path: Path,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        process_env = None
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[codex-workbench] start {' '.join(self._redacted_command(command))}\n")
            log_file.flush()
            return subprocess.Popen(
                command,
                cwd=self.tools_root,
                env=process_env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

    @staticmethod
    def _redacted_command(command: list[str]) -> list[str]:
        redacted = list(command)
        for flag in ("--api-key",):
            if flag in redacted:
                index = redacted.index(flag)
                if index + 1 < len(redacted):
                    redacted[index + 1] = "<redacted>"
        return redacted

    def _stop_gateway_process(self, process: subprocess.Popen[str]) -> int | None:
        if process.poll() is not None:
            return process.returncode
        try:
            process.send_signal(signal.SIGINT)
        except OSError:
            return process.poll()

        try:
            return process.wait(timeout=GATEWAY_PROCESS_ABORT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

        try:
            process.terminate()
            return process.wait(timeout=GATEWAY_PROCESS_ABORT_GRACE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                return process.wait(timeout=GATEWAY_PROCESS_ABORT_GRACE_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                return process.poll()

    def _mark_latest_gateway_abort_requested(self, run_id: str, session_id: str | None) -> None:
        if not session_id:
            return
        try:
            run_dir = self._run_dir(run_id)
            session_dir = self._gateway_session_dir(run_dir, session_id)
            metadata_path = session_dir / "session-metadata.json"
            metadata = self._load_json(metadata_path)
            if not isinstance(metadata, dict):
                return
            metadata.update(
                {
                    "abort_requested": True,
                    "abort_requested_at": self._utc_now(),
                    "abort_requested_by": "codex-workbench",
                }
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (DevShelfRunNotFound, OSError):
            return

    def _gateway_control_response(
        self,
        launch: DevShelfGatewayLaunch,
        *,
        status: str,
        returncode: int | None = None,
        message: str | None = None,
    ) -> DevShelfGatewayControlResponse:
        return DevShelfGatewayControlResponse(
            run_id=launch.run_id,
            status=status,
            pid=launch.process.pid,
            returncode=returncode if returncode is not None else launch.process.poll(),
            launch_id=launch.launch_id,
            started_at=launch.started_at,
            finished_at=self._utc_now() if launch.process.poll() is not None else None,
            log_path=str(launch.log_path),
            command=launch.command,
            gateway_session_id=launch.gateway_session_id,
            message=message,
        )

    def _read_log_tail(self, log_path: Path, limit: int = 4000) -> str | None:
        try:
            content = log_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return content[-limit:].strip() if content else None

    def _load_state(self, run_dir: Path, run_id: str) -> dict[str, Any]:
        state = self._load_json(run_dir / "run-state.json")
        if not isinstance(state, dict):
            raise DevShelfRunNotFound(f"Run state not found: {run_id}")
        return state

    def _pending_human_gates(self, state: dict[str, Any]) -> list[DevShelfHumanGate]:
        gates = []
        for item in state.get("human_gates", []):
            if isinstance(item, dict) and item.get("gate_id") and item.get("status") == "pending":
                gates.append(self._gate_model(state, item))

        seen = {item.gate_id for item in gates}
        for artifact in state.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            artifact_id = artifact.get("artifact_id")
            if (
                not artifact_id
                or artifact_id not in CONFIRMATION_ARTIFACTS
                or artifact.get("status") not in {"draft", "in_review"}
            ):
                continue
            if artifact_id == "existing_project_analysis" and not state.get(
                "requires_existing_project_analysis"
            ):
                continue
            gate_id = f"{artifact_id}{CONFIRMATION_SUFFIX}"
            if gate_id in seen:
                continue
            gates.append(
                DevShelfHumanGate(
                    gate_id=gate_id,
                    label=f"{artifact_id} 人工确认",
                    required_for_stage=state.get("current_stage"),
                    owner="developer",
                    status="pending",
                    decision_note=f"`{artifact_id}` 已产出，必须人工确认后才能进入下一阶段。",
                    artifact_id=artifact_id,
                )
            )
        return gates

    def _gate_model(self, state: dict[str, Any], gate: dict[str, Any]) -> DevShelfHumanGate:
        return DevShelfHumanGate(
            gate_id=str(gate.get("gate_id") or ""),
            label=gate.get("label"),
            required_for_stage=gate.get("required_for_stage"),
            owner=gate.get("owner"),
            status=gate.get("status"),
            decision_note=gate.get("decision_note"),
            artifact_id=self._artifact_id_for_gate(state, str(gate.get("gate_id") or "")),
        )

    def _router_result(
        self,
        state: dict[str, Any],
        latest_packet: DevShelfExecutionPacket | None,
        pending_gates: list[DevShelfHumanGate],
    ) -> DevShelfRouterResult:
        if pending_gates:
            return DevShelfRouterResult(
                decision_type="wait_for_human",
                target=[gate.gate_id for gate in pending_gates],
                reason="当前运行正在等待人工确认，暂不自动推进。",
                pending_human_gates=pending_gates,
                next_step_after_approval="人工确认写回状态后，必须重新调用 router。",
            )

        packet_content = latest_packet.content if latest_packet else None
        router_result = packet_content.get("router_result") if isinstance(packet_content, dict) else None
        if isinstance(router_result, dict):
            raw_gates = router_result.get("pending_human_gates") or []
            packet_gates = [
                self._gate_model(state, gate)
                for gate in raw_gates
                if isinstance(gate, dict) and gate.get("gate_id")
            ]
            return DevShelfRouterResult(
                decision_type=router_result.get("decision_type"),
                target=router_result.get("target"),
                reason=router_result.get("reason"),
                pending_human_gates=packet_gates,
                next_step_after_approval=router_result.get("next_step_after_approval"),
                content=router_result,
            )

        if latest_packet:
            return DevShelfRouterResult(
                decision_type=latest_packet.decision_type,
                target=latest_packet.target,
                content=latest_packet.content,
            )

        return DevShelfRouterResult(
            decision_type=None,
            target=None,
            reason=state.get("status"),
        )

    def _find_artifact(self, state: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
        for artifact in state.get("artifacts", []):
            if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id:
                return artifact
        return None

    def _find_gate(self, state: dict[str, Any], gate_id: str) -> dict[str, Any] | None:
        for gate in state.get("human_gates", []):
            if isinstance(gate, dict) and gate.get("gate_id") == gate_id:
                return gate
        return None

    def _artifact_id_for_gate(self, state: dict[str, Any], gate_id: str) -> str | None:
        for artifact in state.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            artifact_id = artifact.get("artifact_id")
            if artifact_id and gate_id == f"{artifact_id}{CONFIRMATION_SUFFIX}":
                return str(artifact_id)
        if gate_id.endswith(CONFIRMATION_SUFFIX):
            candidate = gate_id[: -len(CONFIRMATION_SUFFIX)]
            if candidate:
                return candidate
        return None

    def _default_decision_note(self, gate_id: str, decision: str) -> str:
        if decision == "approved":
            return f"{gate_id} 已人工确认通过。"
        return f"{gate_id} 已人工拒绝，等待修订。"

    def _write_artifact_feedback(
        self,
        *,
        run_dir: Path,
        artifact_id: str,
        candidate_id: str | None,
        feedback: str,
    ) -> Path:
        feedback_dir = run_dir / "artifacts" / "workbench-feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        safe_artifact_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", artifact_id).strip("-") or "artifact"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        path = feedback_dir / f"{timestamp}-{safe_artifact_id}.md"
        lines = [
            "# Workbench 修改意见",
            "",
            f"- artifact_id: `{artifact_id}`",
            f"- candidate_id: `{candidate_id or '-'}`",
            f"- created_at: `{self._utc_now()}`",
            "",
            "## 反馈",
            "",
            feedback,
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _emit_artifact_decision(
        self,
        *,
        run_state_path: Path,
        artifact_id: str,
        artifact_status: str,
        artifact: dict[str, Any],
        note: str,
        feedback_path: str | None = None,
    ) -> None:
        args = [
            "artifact",
            "--run-state",
            str(run_state_path),
            "--actor",
            "human",
            "--artifact-id",
            artifact_id,
            "--artifact-status",
            artifact_status,
            "--note",
            note,
            "--apply",
            "--pretty",
        ]
        if artifact.get("title"):
            args.extend(["--title", str(artifact["title"])])
        if artifact.get("path"):
            args.extend(["--path", str(artifact["path"])])
        if artifact.get("produced_by"):
            args.extend(["--produced-by", str(artifact["produced_by"])])
        if feedback_path:
            args.extend(["--feedback-path", feedback_path])
        self._run_dev_shelf_tool("dev_shelf_emit_event.py", args)

    def _emit_gate_decision(
        self,
        *,
        run_state_path: Path,
        gate_id: str,
        gate_status: str,
        gate: dict[str, Any],
        note: str,
    ) -> None:
        args = [
            "gate",
            "--run-state",
            str(run_state_path),
            "--actor",
            "human",
            "--gate-id",
            gate_id,
            "--gate-status",
            gate_status,
            "--decision-note",
            note,
            "--apply",
            "--pretty",
        ]
        if gate.get("label"):
            args.extend(["--label", str(gate["label"])])
        if gate.get("required_for_stage"):
            args.extend(["--required-for-stage", str(gate["required_for_stage"])])
        if gate.get("owner"):
            args.extend(["--owner", str(gate["owner"])])
        self._run_dev_shelf_tool("dev_shelf_emit_event.py", args)

    def _advance_after_confirmed_gateway_candidate(self, *, run_state_path: Path, artifact_id: str) -> None:
        stage_advances = {
            "requirement_confirmation_checklist": {
                "stage": "confirmed_requirement",
                "run_status": "ready_for_next_stage",
                "next_allowed": ["skill_selection", "spec_drafting"],
                "note": "需求确认清单已确认，进入已确认需求阶段。",
            },
            "implementation_plan": {
                "stage": "spec_drafting",
                "run_status": "in_progress",
                "next_allowed": [],
                "note": "书架推进计划已确认，进入 spec 起草阶段。",
            },
            "spec": {
                "stage": "reuse_decision",
                "run_status": "in_progress",
                "next_allowed": [],
                "note": "spec 已确认，进入复用判断阶段。",
            },
            "reuse_decision": {
                "stage": "implementation_planning",
                "run_status": "in_progress",
                "next_allowed": [],
                "note": "复用判断已确认，进入执行待办规划阶段。",
            },
        }
        advance = stage_advances.get(artifact_id)
        if advance is None:
            return
        args = [
            "stage",
            "--run-state",
            str(run_state_path),
            "--actor",
            "human",
            "--stage",
            str(advance["stage"]),
            "--run-status",
            str(advance["run_status"]),
            "--note",
            str(advance["note"]),
            "--apply",
            "--pretty",
        ]
        next_allowed = advance.get("next_allowed")
        if next_allowed:
            args[args.index("--note") : args.index("--note")] = [
                "--next-allowed",
                *[str(item) for item in next_allowed],
            ]
        self._run_dev_shelf_tool(
            "dev_shelf_emit_event.py",
            args,
        )

    def _write_next_packet(self, run_state_path: Path) -> None:
        self._run_dev_shelf_tool(
            "dev_shelf_runner.py",
            [
                "--run-state",
                str(run_state_path),
                "--write-packet-pair",
                "--pretty",
            ],
        )

    def _run_dev_shelf_tool(self, script_name: str, args: list[str]) -> dict[str, Any]:
        script_path = self.tools_root / "scripts" / script_name
        if not script_path.is_file():
            raise DevShelfToolError(f"dev-shelf script not found: {script_path}")

        completed = subprocess.run(
            [sys.executable, str(script_path), *args],
            cwd=self.tools_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or script_name
            raise DevShelfToolError(detail)

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _updated_at(self, state: dict[str, Any]) -> str | None:
        history = state.get("history", [])
        if isinstance(history, list):
            for item in reversed(history):
                if isinstance(item, dict) and item.get("at"):
                    return item["at"]
        artifacts = state.get("artifacts", [])
        if isinstance(artifacts, list):
            updated = [
                item.get("updated_at")
                for item in artifacts
                if isinstance(item, dict) and item.get("updated_at")
            ]
            if updated:
                return sorted(updated)[-1]
        return None

    def get_model_config(self) -> DevShelfModelConfig:
        return self._model_config_response(self._load_model_config())

    def update_model_config(self, payload: DevShelfModelConfigUpdateRequest) -> DevShelfModelConfig:
        pi_settings = self._load_pi_settings()
        provider = self._normalized_gateway_value(
            payload.provider,
            PI_PROVIDER_RE,
            field_name="provider",
            default=self._pi_default_provider(pi_settings),
        )
        model = self._normalized_gateway_value(
            payload.model,
            PI_MODEL_RE,
            field_name="model",
            default=self._provider_default_model(provider or "openai-codex", pi_settings),
        )
        account = self._normalized_gateway_account(payload.account) if provider == "openai-codex" else None
        config = self._load_model_config()
        config["provider"] = provider
        config.setdefault("models", {})[provider] = model
        if provider == "openai-codex":
            config["account"] = account or self._default_gateway_account()

        self._write_model_config(config)
        return self._model_config_response(config)

    def _load_model_config(self) -> dict[str, Any]:
        config = self._load_json(self.model_config_path) or {}
        if not isinstance(config.get("models"), dict):
            config["models"] = {}
        if "api_keys" in config:
            config.pop("api_keys", None)
            self._write_model_config(config)
        return config

    def _write_model_config(self, config: dict[str, Any]) -> None:
        self.workbench_config_dir.mkdir(parents=True, exist_ok=True)
        self.model_config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(self.workbench_config_dir, 0o700)
            os.chmod(self.model_config_path, 0o600)
        except OSError:
            pass

    def _model_config_response(self, config: dict[str, Any]) -> DevShelfModelConfig:
        pi_settings = self._load_pi_settings()
        provider = self._model_config_provider(config)
        model = self._model_config_model(config, provider, pi_settings)
        account = config.get("account") if isinstance(config.get("account"), str) else self._default_gateway_account()
        if provider != "openai-codex":
            account = None
        return DevShelfModelConfig(
            provider=provider,
            model=model,
            account=account,
            providers=self._model_config_providers(config),
        )

    def _model_config_provider(self, config: dict[str, Any]) -> str:
        provider = config.get("provider")
        if isinstance(provider, str) and provider in DEFAULT_GATEWAY_MODELS:
            return provider
        return self._pi_default_provider(self._load_pi_settings())

    def _model_config_model(self, config: dict[str, Any], provider: str, pi_settings: dict[str, Any] | None = None) -> str:
        models = config.get("models") if isinstance(config.get("models"), dict) else {}
        model = models.get(provider) if isinstance(models.get(provider), str) else None
        return model or self._provider_default_model(provider, pi_settings)

    def _model_config_providers(self, config: dict[str, Any]) -> list[DevShelfModelProvider]:
        pi_settings = self._load_pi_settings()
        accounts = self._gateway_accounts()
        return [
            DevShelfModelProvider(
                provider="openai-codex",
                label=PROVIDER_LABELS["openai-codex"],
                requires_account=True,
                auth_configured=self._pi_auth_configured("openai-codex"),
                auth_source=str(self.pi_auth_path) if self.pi_auth_path.is_file() else None,
                default_model=self._model_config_model(config, "openai-codex", pi_settings),
                default_account=config.get("account") if isinstance(config.get("account"), str) else self._default_gateway_account(),
                accounts=accounts,
            ),
            DevShelfModelProvider(
                provider="deepseek",
                label=PROVIDER_LABELS["deepseek"],
                requires_account=False,
                auth_configured=self._pi_auth_configured("deepseek"),
                auth_source=str(self.pi_auth_path) if self.pi_auth_path.is_file() else None,
                default_model=self._model_config_model(config, "deepseek", pi_settings),
                default_account=None,
                accounts=[],
            ),
        ]

    def _load_pi_settings(self) -> dict[str, Any]:
        settings = self._load_json(self.pi_settings_path) or {}
        return settings if isinstance(settings, dict) else {}

    def _load_pi_auth(self) -> dict[str, Any]:
        auth = self._load_json(self.pi_auth_path) or {}
        return auth if isinstance(auth, dict) else {}

    def _pi_default_provider(self, settings: dict[str, Any] | None = None) -> str:
        value = (settings or self._load_pi_settings()).get("defaultProvider")
        return value if isinstance(value, str) and value in DEFAULT_GATEWAY_MODELS else "openai-codex"

    def _provider_default_model(self, provider: str, settings: dict[str, Any] | None = None) -> str:
        settings = settings or self._load_pi_settings()
        if provider == self._pi_default_provider(settings):
            value = settings.get("defaultModel")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return DEFAULT_GATEWAY_MODELS.get(provider, "gpt-5.4")

    def _pi_auth_configured(self, provider: str) -> bool:
        auth = self._load_pi_auth()
        provider_auth = auth.get(provider)
        if not isinstance(provider_auth, dict):
            return False
        if provider == "deepseek":
            return provider_auth.get("type") == "api_key" and bool(provider_auth.get("key"))
        if provider == "openai-codex":
            return provider_auth.get("type") == "oauth" and bool(provider_auth.get("access") or provider_auth.get("refresh"))
        return bool(provider_auth)

    def _gateway_accounts(self) -> list[str]:
        accounts = ["default"] if (Path.home() / ".pi" / "agent").is_dir() else []
        account_base = Path.home() / ".pi"
        if account_base.is_dir():
            for path in sorted(account_base.glob("agent-codex-*")):
                if path.is_dir():
                    name = path.name.removeprefix("agent-codex-")
                    if PI_ACCOUNT_RE.fullmatch(name):
                        accounts.append(name)
        return sorted(dict.fromkeys(accounts))

    def _default_gateway_account(self) -> str | None:
        accounts = self._gateway_accounts()
        if "a" in accounts:
            return "a"
        return accounts[0] if accounts else None

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    PI_MODEL_ROW_RE = re.compile(r"\s{2,}")

    def list_available_models(self, provider: str | None = None) -> DevShelfModelList:
        pi_bin = "pi"
        cmd = f"{pi_bin} --list-models 2>&1"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
            output = result.stdout or ""
        except (subprocess.TimeoutExpired, OSError):
            return DevShelfModelList()

        models: list[DevShelfModelItem] = []
        for line in output.strip().split("\n"):
            if not line or line.startswith("provider") or line.startswith("---"):
                continue
            parts = [p for p in self.PI_MODEL_ROW_RE.split(line) if p]
            if len(parts) < 2:
                continue
            item = DevShelfModelItem(
                provider=parts[0].strip(),
                model=parts[1].strip(),
                context_window=parts[2].strip() if len(parts) > 2 else "-",
                max_output=parts[3].strip() if len(parts) > 3 else "-",
                thinking=parts[4].strip() if len(parts) > 4 else "-",
                images=parts[5].strip() if len(parts) > 5 else "-",
            )
            if not provider or item.provider == provider:
                models.append(item)
        return DevShelfModelList(models=models)

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _parse_utc(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


dev_shelf_service = DevShelfReadService()
