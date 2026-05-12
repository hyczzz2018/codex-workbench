from __future__ import annotations


from app.services.dev_shelf_base import *
from app.services.dev_shelf_base import _GatewayStreamSession
class DevShelfCommonMixin:

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
        self._gateway_stream_sessions: dict[tuple[str, str], _GatewayStreamSession] = {}
        self._gateway_stream_sessions_lock = threading.Lock()


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


    def _same_path(self, left: str, right: str) -> bool:
        try:
            return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)
        except OSError:
            return left == right


    def _load_state(self, run_dir: Path, run_id: str) -> dict[str, Any]:
        state = self._load_json(run_dir / "run-state.json")
        if not isinstance(state, dict):
            raise DevShelfRunNotFound(f"Run state not found: {run_id}")
        return state


    def _action_policy(
        self,
        state: dict[str, Any],
        latest_packet: DevShelfExecutionPacket | None,
        run_id: str,
    ) -> dict[str, Any] | None:
        module_path = self._action_policy_module_path()
        if module_path is None:
            return None
        spec = importlib.util.spec_from_file_location("dev_shelf_action_policy_for_workbench", module_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        gateway_status = self._gateway_status_dict_for_policy(run_id)
        packet_payload = latest_packet.model_dump() if latest_packet else None
        return module.build_action_policy(state, packet_payload, gateway_status)


    def _action_policy_module_path(self) -> Path | None:
        candidates = [
            self.tools_root / "scripts" / "dev_shelf_action_policy.py",
            DEFAULT_DEV_SHELF_ROOT / "scripts" / "dev_shelf_action_policy.py",
        ]
        for module_path in candidates:
            if module_path.is_file():
                return module_path
        return None


    def _gateway_status_dict_for_policy(self, run_id: str) -> dict[str, Any] | None:
        try:
            status = self.get_gateway_status(run_id)
        except DevShelfRunNotFound:
            return None
        return status.model_dump()


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


    def _artifact_revision_id(self, artifact: dict[str, Any]) -> str:
        current_revision_id = artifact.get("current_revision_id")
        if isinstance(current_revision_id, str) and current_revision_id:
            return current_revision_id
        revisions = artifact.get("revisions")
        if isinstance(revisions, list):
            for revision in reversed(revisions):
                revision_id = revision.get("revision_id") if isinstance(revision, dict) else None
                if isinstance(revision_id, str) and revision_id:
                    return revision_id
        return "rev_0001"


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


    def _first_string_value(self, source: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = self._string_value(source.get(key)).strip()
            if value:
                return value
        return ""


    def _port_value(self, value: Any) -> int | None:
        if isinstance(value, int):
            return value if 1 <= value <= 65535 else None
        if isinstance(value, str) and value.isdigit():
            port = int(value)
            return port if 1 <= port <= 65535 else None
        return None


    def _string_value(self, value: Any) -> str:
        return value if isinstance(value, str) else ""


    def _gateway_content_text(self, content: Any) -> str:
        if not isinstance(content, list):
            return ""
        return "\n\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )


    def _count_jsonl_lines(self, path: Path) -> int:
        if not path.is_file():
            return 0
        try:
            with path.open("r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0
