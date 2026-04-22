from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.schemas.dev_shelf import (
    DevShelfArtifact,
    DevShelfExecutionPacket,
    DevShelfHumanGate,
    DevShelfHumanGateDecisionRequest,
    DevShelfRouterResult,
    DevShelfRunDetail,
    DevShelfRunSummary,
)


DEFAULT_DEV_SHELF_ROOT = Path("/home/hyc/projects/dev-shelf")
RUN_ID_RE = re.compile(r"^run_[a-z0-9_-]+$")
GATE_ID_RE = re.compile(r"^[a-z0-9_-]+$")
PACKET_RE = re.compile(r"^(?P<sequence>\d{4})-execution-packet\.json$")
CONFIRMATION_SUFFIX = "_approval"
CONFIRMATION_ARTIFACTS = {"existing_project_analysis", "spec", "reuse_decision", "execution_todo"}
ARTIFACT_PREVIEW_LIMIT = 64 * 1024


class DevShelfRunNotFound(ValueError):
    pass


class DevShelfGateConflict(ValueError):
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


dev_shelf_service = DevShelfReadService()
