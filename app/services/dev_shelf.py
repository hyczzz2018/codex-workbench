from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas.dev_shelf import (
    DevShelfArtifact,
    DevShelfExecutionPacket,
    DevShelfGatewayAbortRequest,
    DevShelfGatewayArtifactPayload,
    DevShelfGatewayControlResponse,
    DevShelfGatewayRuntimeEvents,
    DevShelfGatewaySessionStatus,
    DevShelfGatewayStartRequest,
    DevShelfHumanGate,
    DevShelfHumanGateDecisionRequest,
    DevShelfProjectCreateRequest,
    DevShelfProjectCreateResponse,
    DevShelfRouterResult,
    DevShelfRunDetail,
    DevShelfRunSummary,
)


DEFAULT_DEV_SHELF_ROOT = Path("/home/hyc/projects/dev-shelf")
RUN_ID_RE = re.compile(r"^run_[a-z0-9_-]+$")
GATE_ID_RE = re.compile(r"^[a-z0-9_-]+$")
PACKET_RE = re.compile(r"^(?P<sequence>\d{4})-execution-packet\.json$")
SESSION_ID_RE = re.compile(r"^session-[a-zA-Z0-9_-]+$")
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
        self._gateway_launches: dict[str, DevShelfGatewayLaunch] = {}

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

    def get_gateway_status(self, run_id: str, session_id: str | None = None) -> DevShelfGatewaySessionStatus:
        run_dir = self._run_dir(run_id)
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

    def _gateway_artifact_payload(
        self,
        run_id: str,
        session_id: str | None,
        *,
        metadata_field: str,
        default_name: str,
    ) -> DevShelfGatewayArtifactPayload:
        run_dir = self._run_dir(run_id)
        session_dir = self._gateway_session_dir(run_dir, session_id)
        metadata = self._load_json(session_dir / "session-metadata.json") or {}
        path = self._gateway_metadata_path(session_dir, metadata.get(metadata_field), default_name=default_name)
        payload = self._load_json(path) if path.is_file() else None
        status = self._gateway_status_from_session(session_dir)
        return DevShelfGatewayArtifactPayload(
            run_id=status.run_id,
            gateway_session_id=status.gateway_session_id,
            path=str(path) if path.exists() else None,
            payload=payload,
        )

    def _project_intake_from_payload(self, payload: DevShelfProjectCreateRequest) -> dict[str, Any]:
        project_name = payload.project_name.strip()
        requirement = payload.requirement.strip()
        if not project_name:
            raise DevShelfProjectConflict("项目名不能为空。")
        if not requirement:
            raise DevShelfProjectConflict("需求不能为空。")

        project_slug = self._normalize_project_slug(payload.project_slug or project_name)
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

    def _spawn_gateway_process(self, command: list[str], log_path: Path) -> subprocess.Popen[str]:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[codex-workbench] start {' '.join(command)}\n")
            log_file.flush()
            return subprocess.Popen(
                command,
                cwd=self.tools_root,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

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

    def _emit_artifact_decision(
        self,
        *,
        run_state_path: Path,
        artifact_id: str,
        artifact_status: str,
        artifact: dict[str, Any],
        note: str,
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

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


dev_shelf_service = DevShelfReadService()
