import json
import signal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import routes
from app.schemas.dev_shelf import (
    DevShelfDirectoryCreateRequest,
    DevShelfGatewayAbortRequest,
    DevShelfGatewayCandidateConfirmRequest,
    DevShelfGatewayCandidateReviseRequest,
    DevShelfGatewayRegisterResultRequest,
    DevShelfGatewayStartRequest,
    DevShelfHumanGateDecisionRequest,
    DevShelfModelConfigUpdateRequest,
    DevShelfProjectCreateRequest,
    DevShelfRunCancelRequest,
)
from app.services.dev_shelf import ARTIFACT_PREVIEW_LIMIT, DevShelfGatewayLaunch, DevShelfReadService


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_pending_gate_run(root, run_id="run_demo_20260415000000"):
    run_dir = root / "runs" / run_id
    project_path = root / "project"
    project_path.mkdir(parents=True, exist_ok=True)
    write_json(
        run_dir / "run-state.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "project_name": "Demo Run",
            "request_summary": "Approve a dev-shelf gate from the browser.",
            "task_type": "feature",
            "task_type_status": "confirmed",
            "project_context": "existing_project",
            "current_stage": "spec_drafting",
            "status": "awaiting_human",
            "selected_units": ["template.spec", "template.reuse-decision", "template.execution-todo"],
            "confirmed_items": [],
            "unresolved_items": [],
            "reuse_candidates": [],
            "artifacts": [
                {
                    "artifact_id": "spec",
                    "title": "项目 spec",
                    "status": "in_review",
                    "path": "docs/demo/spec.md",
                    "produced_by": "template.spec",
                    "updated_at": "2026-04-15T01:00:00Z",
                }
            ],
            "human_gates": [
                {
                    "gate_id": "spec_approval",
                    "label": "spec 人工确认",
                    "required_for_stage": "reuse_decision",
                    "owner": "developer",
                    "status": "pending",
                    "decision_note": "`spec` 已产出，必须人工确认后才能进入下一阶段。",
                }
            ],
            "next_allowed": [],
            "metrics": {},
            "history": [
                {
                    "from_stage": "confirmed_requirement",
                    "to_stage": "spec_drafting",
                    "action": "enter",
                    "actor": "system",
                    "reason": "entered spec drafting",
                    "at": "2026-04-15T01:00:00Z",
                }
            ],
        },
    )
    write_json(
        run_dir / "packets" / "0001-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "wait_for_human",
            "ready": False,
            "target": ["spec_approval"],
            "workspace": {
                "kind": "existing_project",
                "project_path": str(project_path),
                "existing_project_path": str(project_path),
                "allowed_read_paths": [str(project_path)],
                "allowed_write_paths": [str(project_path)],
                "confirmation_status": "confirmed",
            },
            "agent_runtime_contract": {
                "cwd": str(project_path),
                "workspace_confirmed": True,
                "allowed_read_paths": [str(project_path)],
                "allowed_write_paths": [str(project_path)],
            },
        },
    )
    return run_dir


def make_gateway_runnable_run(root, run_id="run_demo_20260415000000"):
    run_dir = root / "runs" / run_id
    project_path = root / "project"
    project_path.mkdir(parents=True, exist_ok=True)
    write_json(
        run_dir / "run-state.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "project_name": "Demo Run",
            "request_summary": "Run pi-agent from a runnable packet.",
            "task_type": "feature",
            "task_type_status": "confirmed",
            "project_context": "existing_project",
            "current_stage": "requirements_drafting",
            "status": "in_progress",
            "artifacts": [],
            "human_gates": [],
            "history": [],
        },
    )
    write_json(
        run_dir / "packets" / "0001-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "run_manifest",
            "ready": True,
            "target": "template.requirement-confirmation-checklist",
            "pending_outputs": [
                {
                    "artifact_id": "requirement_confirmation_checklist",
                    "title": "需求确认清单",
                }
            ],
            "workspace": {
                "kind": "existing_project",
                "project_path": str(project_path),
                "existing_project_path": str(project_path),
                "allowed_read_paths": [str(project_path)],
                "allowed_write_paths": [str(project_path)],
                "confirmation_status": "confirmed",
            },
            "agent_runtime_contract": {
                "cwd": str(project_path),
                "workspace_confirmed": True,
                "allowed_read_paths": [str(project_path)],
                "allowed_write_paths": [str(project_path)],
            },
        },
    )
    return run_dir


def make_gateway_session(
    run_dir,
    session_id="session-demo",
    packet_path=None,
    packet_target="template.requirement-confirmation-checklist",
    status="completed",
) -> None:
    root = run_dir.parent.parent
    session_dir = run_dir / "artifacts" / "pi-agent-gateway" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    runtime_events_path = session_dir / "runtime-events.jsonl"
    gateway_result_path = session_dir / "gateway-result.json"
    candidates_path = session_dir / "gateway-event-candidates.json"
    preview_path = root / "docs" / "demo" / "requirement-confirmation.md"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text("# 需求确认清单\n\n请确认目标用户和页面范围。", encoding="utf-8")
    runtime_events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": run_dir.name,
                        "gateway_session_id": session_id,
                        "pi_session_id": "pi-demo",
                        "stream": "runtime",
                        "kind": "response",
                        "sequence": 1,
                        "raw": {"command": "get_state"},
                    }
                ),
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": run_dir.name,
                        "gateway_session_id": session_id,
                        "pi_session_id": "pi-demo",
                        "stream": "runtime",
                        "kind": "text",
                        "sequence": 2,
                        "raw": {"type": "text_delta", "delta": "hello"},
                    }
                ),
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": run_dir.name,
                        "gateway_session_id": session_id,
                        "pi_session_id": "pi-demo",
                        "stream": "runtime",
                        "kind": "tool",
                        "sequence": 3,
                        "raw": {
                            "type": "tool_execution_end",
                            "toolCallId": "call-1",
                            "toolName": "write",
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Successfully wrote 42 bytes to /tmp/demo.txt",
                                    }
                                ]
                            },
                            "isError": False,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        gateway_result_path,
        {
            "schema_version": "1.0",
            "run_id": run_dir.name,
            "gateway_session_id": session_id,
            "summary": {"output_count": 1, "produced_count": 1},
            "outputs": [],
        },
    )
    write_json(
        candidates_path,
        {
            "schema_version": "1.0",
            "generated_at": "2026-04-24T00:00:10Z",
            "run_id": run_dir.name,
            "gateway_session_id": session_id,
            "summary": {"candidate_count": 1, "skipped_count": 0},
            "candidates": [
                {
                    "candidate_id": "candidate-0001-demo",
                    "artifact_id": "requirement_confirmation_checklist",
                    "event": {
                        "schema_version": "1.0",
                        "event_type": "artifact_status_changed",
                        "actor": "ai",
                        "artifact_id": "requirement_confirmation_checklist",
                        "artifact_status": "draft",
                        "title": "需求确认清单",
                        "produced_by": "template.requirement-confirmation-checklist",
                        "path": str(preview_path),
                    },
                    "source_output": {
                        "artifact_id": "requirement_confirmation_checklist",
                        "title": "需求确认清单",
                        "review_required": True,
                        "status_on_produce": "draft",
                        "declared_paths": [{"path": str(preview_path), "exists": True, "is_file": True}],
                    },
                    "review_required": True,
                }
            ],
            "skipped": [],
        },
    )
    write_json(
        session_dir / "session-metadata.json",
        {
            "runtime_event_schema_version": "1.0",
            "event_count": 3,
            "status": status,
            "started_at": "2026-04-24T00:00:00Z",
            "finished_at": "2026-04-24T00:00:10Z",
            "run_id": run_dir.name,
            "gateway_session_id": session_id,
            "provider": "openai-codex",
            "model": "gpt-5.4",
            "pi_account": "a",
            "pi_session_id": "pi-demo",
            "packet_path": str(packet_path or run_dir / "packets" / "0001-execution-packet.json"),
            "packet_target": packet_target,
            "runtime_events_path": str(runtime_events_path),
            "gateway_result_json": str(gateway_result_path),
            "gateway_event_candidates_json": str(candidates_path),
            "artifact_result_summary": {"output_count": 1, "produced_count": 1},
            "event_candidate_summary": {"candidate_count": 1, "skipped_count": 0},
        },
    )


def collect_gateway_stream(service: DevShelfReadService, run_id: str, **kwargs) -> str:
    return "".join(
        service.iter_gateway_stream_events(
            run_id,
            poll_interval_seconds=0.1,
            **kwargs,
        )
    )


def make_execution_registerable_run(root, run_id="run_demo_20260425000000"):
    run_dir = root / "runs" / run_id
    project_path = root / "project"
    implementation_path = root / "docs" / "demo" / "implementation-result.md"
    project_path.mkdir(parents=True, exist_ok=True)
    write_json(
        run_dir / "run-state.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "project_name": "Demo Run",
            "request_summary": "Register implementation result from a completed Gateway session.",
            "current_stage": "execution",
            "status": "in_progress",
            "artifacts": [],
            "human_gates": [],
            "history": [],
        },
    )
    write_json(
        run_dir / "packets" / "0001-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "run_manifest",
            "ready": True,
            "target": "stage.execution",
            "stage": "execution",
            "pending_outputs": [
                {
                    "artifact_id": "implementation_result",
                    "title": "实现结果",
                    "status_on_produce": "done",
                    "path": str(implementation_path),
                }
            ],
            "workspace": {
                "kind": "existing_project",
                "project_path": str(project_path),
                "allowed_read_paths": [str(project_path)],
                "allowed_write_paths": [str(project_path)],
                "confirmation_status": "confirmed",
            },
            "agent_runtime_contract": {
                "cwd": str(project_path),
                "workspace_confirmed": True,
                "allowed_read_paths": [str(project_path)],
                "allowed_write_paths": [str(project_path)],
            },
        },
    )
    session_dir = run_dir / "artifacts" / "pi-agent-gateway" / "session-demo"
    session_dir.mkdir(parents=True, exist_ok=True)
    runtime_events_path = session_dir / "runtime-events.jsonl"
    runtime_events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "gateway_session_id": "session-demo",
                        "stream": "runtime",
                        "kind": "text",
                        "sequence": 1,
                        "raw": {
                            "delta": f"Successfully wrote 10 bytes to {project_path / 'index.html'}\n",
                        },
                    }
                ),
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": run_id,
                        "gateway_session_id": "session-demo",
                        "stream": "runtime",
                        "kind": "text",
                        "sequence": 2,
                        "raw": {"delta": "Implemented the static page."},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        session_dir / "gateway-result.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "gateway_session_id": "session-demo",
            "summary": {"output_count": 1, "produced_count": 0},
            "outputs": [],
        },
    )
    write_json(
        session_dir / "gateway-event-candidates.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "gateway_session_id": "session-demo",
            "summary": {"candidate_count": 0, "skipped_count": 0},
            "candidates": [],
            "skipped": [],
        },
    )
    write_json(
        session_dir / "session-metadata.json",
        {
            "runtime_event_schema_version": "1.0",
            "event_count": 2,
            "status": "completed",
            "started_at": "2026-04-25T00:00:00Z",
            "finished_at": "2026-04-25T00:00:10Z",
            "run_id": run_id,
            "gateway_session_id": "session-demo",
            "provider": "openai-codex",
            "model": "gpt-5.4",
            "packet_path": str(run_dir / "packets" / "0001-execution-packet.json"),
            "packet_target": "stage.execution",
            "runtime_events_path": str(runtime_events_path),
            "gateway_result_json": str(session_dir / "gateway-result.json"),
            "gateway_event_candidates_json": str(session_dir / "gateway-event-candidates.json"),
            "artifact_result_summary": {"output_count": 1, "produced_count": 0},
            "event_candidate_summary": {"candidate_count": 0, "skipped_count": 0},
        },
    )
    return run_dir, implementation_path


class FakeGatewayProcess:
    pid = 4321

    def __init__(self) -> None:
        self.returncode = None
        self.signals = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)
        self.returncode = 130

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def test_dev_shelf_run_routes_are_read_only(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = root / "runs" / "run_demo_20260413000000"
    write_json(
        run_dir / "run-state.json",
        {
            "run_id": "run_demo_20260413000000",
            "project_name": "Demo Run",
            "request_summary": "Show this run in the browser.",
            "task_type": "feature",
            "task_type_status": "confirmed",
            "current_stage": "spec_confirmation",
            "status": "awaiting_human",
            "artifacts": [
                {
                    "artifact_id": "spec",
                    "title": "项目 spec",
                    "status": "approved",
                    "path": "docs/demo/spec.md",
                    "produced_by": "template.spec",
                    "updated_at": "2026-04-13T01:00:00Z",
                }
            ],
            "history": [
                {
                    "to_stage": "spec_confirmation",
                    "action": "enter",
                    "actor": "system",
                    "at": "2026-04-13T01:10:00Z",
                }
            ],
        },
    )
    write_json(
        run_dir / "packets" / "0002-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "run_manifest",
            "ready": True,
            "target": "template.reuse-decision",
        },
    )
    (run_dir / "packets" / "0002-execution-packet.md").write_text(
        "# Execution Packet\n\n- target: `template.reuse-decision`\n",
        encoding="utf-8",
    )
    (root / "docs" / "demo").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "demo" / "spec.md").write_text("# Spec\n\n已确认的 spec。", encoding="utf-8")

    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    runs = routes.list_dev_shelf_runs()
    assert len(runs.items) == 1
    assert runs.items[0].run_id == "run_demo_20260413000000"
    assert runs.items[0].current_stage == "spec_confirmation"

    detail = routes.get_dev_shelf_run("run_demo_20260413000000")
    assert detail.status == "awaiting_human"
    assert detail.artifacts[0].artifact_id == "spec"
    assert detail.artifacts[0].content == "# Spec\n\n已确认的 spec。"
    assert detail.artifacts[0].content_format == "markdown"
    assert detail.artifacts[0].content_truncated is False
    assert detail.artifacts[0].content_error is None
    assert detail.pending_human_gates == []
    assert detail.router is not None
    assert detail.router.decision_type == "run_manifest"
    assert detail.latest_packet is not None
    assert detail.latest_packet.sequence == 2
    assert detail.latest_packet.target == "template.reuse-decision"
    assert detail.latest_packet.markdown is not None

    state_after = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    assert state_after["status"] == "awaiting_human"


def test_dev_shelf_run_route_rejects_unknown_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(tmp_path / "dev-shelf"))

    with pytest.raises(HTTPException) as exc:
        routes.get_dev_shelf_run("run_missing_20260413000000")

    assert exc.value.status_code == 404


def test_dev_shelf_run_detail_exposes_pending_human_gate(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    make_pending_gate_run(root)
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    detail = routes.get_dev_shelf_run("run_demo_20260415000000")

    assert detail.router is not None
    assert detail.router.decision_type == "wait_for_human"
    assert detail.router.target == ["spec_approval"]
    assert len(detail.pending_human_gates) == 1
    assert detail.pending_human_gates[0].gate_id == "spec_approval"
    assert detail.pending_human_gates[0].artifact_id == "spec"


def test_dev_shelf_workflow_continue_applies_enter_stage_packet(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_id = "run_demo_20260425000000"
    run_dir = root / "runs" / run_id
    project_path = root / "project"
    project_path.mkdir(parents=True)
    write_json(
        run_dir / "run-state.json",
        {
            "schema_version": "1.0",
            "run_id": run_id,
            "project_name": "Demo Run",
            "request_summary": "Enter execution from the browser.",
            "current_stage": "implementation_planning",
            "status": "ready_for_next_stage",
            "artifacts": [],
            "history": [],
        },
    )
    write_json(
        run_dir / "packets" / "0001-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "enter_stage",
            "ready": True,
            "target": "stage.execution",
            "state_event_to_emit": {
                "event_type": "stage_changed",
                "stage": "execution",
                "run_status": "in_progress",
            },
        },
    )
    service = DevShelfReadService(root, tools_root=root)
    calls = []

    def fake_run_tool(script_name, args):
        calls.append((script_name, args))
        assert script_name == "dev_shelf_workflow_action.py"
        assert args[:2] == ["--pretty", "continue-enter-stage"]
        assert "--apply" in args
        state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
        state["current_stage"] = "execution"
        state["status"] = "in_progress"
        write_json(run_dir / "run-state.json", state)
        write_json(
            run_dir / "packets" / "0002-execution-packet.json",
            {
                "packet_version": "1.0",
                "decision_type": "run_manifest",
                "ready": True,
                "target": "stage.execution",
                "pending_outputs": [{"artifact_id": "implementation_result"}],
                "workspace": {"project_path": str(project_path)},
                "agent_runtime_contract": {"cwd": str(project_path)},
            },
        )
        return {"status": "applied"}

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    detail = routes.continue_dev_shelf_workflow(run_id)

    assert len(calls) == 1
    assert detail.current_stage == "execution"
    assert detail.status == "in_progress"
    assert detail.latest_packet is not None
    assert detail.latest_packet.decision_type == "run_manifest"
    assert detail.latest_packet.content is not None
    assert detail.latest_packet.content["pending_outputs"][0]["artifact_id"] == "implementation_result"


def test_dev_shelf_create_run_builds_project_intake(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    service = DevShelfReadService(root, tools_root=root)
    captured = {}

    def fake_run_tool(script_name, args):
        intake_path = args[args.index("--intake") + 1]
        captured["script_name"] = script_name
        captured["args"] = args
        captured["intake"] = json.loads(open(intake_path, encoding="utf-8").read())
        return {
            "status": "created",
            "project_name": captured["intake"]["project_name"],
            "project_slug": captured["intake"]["project_slug"],
            "run_id": "run_demo_web_20260424000000",
            "run_dir": str(root / "runs" / "run_demo_web_20260424000000"),
            "requirement_draft": str(root / "docs" / "demo_web" / "requirement-draft.md"),
            "next_decision_type": "run_manifest",
            "next_target": "template.requirement-confirmation-checklist",
            "message": "created",
        }

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    result = routes.create_dev_shelf_run(
        DevShelfProjectCreateRequest(
            project_name="Demo Web",
            requirement="做一个网页工作台",
            task_type="feature",
            task_type_status="confirmed",
            project_context="existing_project",
            project_path=str(tmp_path / "project"),
            workspace_confirmed=True,
        )
    )

    assert result.status == "created"
    assert result.run_id == "run_demo_web_20260424000000"
    assert captured["script_name"] == "dev_shelf_start_project.py"
    assert captured["intake"]["schema_version"] == "1.0"
    assert captured["intake"]["project_slug"] == "demo_web"
    assert captured["intake"]["requirement_draft"] == "做一个网页工作台"
    assert captured["intake"]["task_type"] == "feature"
    assert captured["intake"]["project_context"] == "existing_project"
    assert captured["intake"]["requires_existing_project_analysis"] is True
    assert captured["intake"]["workspace"]["confirmation_status"] == "confirmed"
    assert captured["intake"]["workspace"]["allowed_write_paths"] == [str((tmp_path / "project").resolve())]


def test_dev_shelf_create_run_defaults_new_project_workspace_path(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    service = DevShelfReadService(root, tools_root=root)
    captured = {}

    def fake_run_tool(script_name, args):
        intake_path = args[args.index("--intake") + 1]
        captured["intake"] = json.loads(open(intake_path, encoding="utf-8").read())
        return {
            "status": "created",
            "project_name": captured["intake"]["project_name"],
            "project_slug": captured["intake"]["project_slug"],
            "run_id": "run_hello_world_20260424000000",
        }

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    routes.create_dev_shelf_run(
        DevShelfProjectCreateRequest(
            project_name="Hello World",
            requirement="创建一个介绍页",
            project_context="new_project",
            workspace_confirmed=True,
        )
    )

    default_path = str((root.parent / "hello_world").resolve())
    assert captured["intake"]["workspace"]["kind"] == "new_project"
    assert captured["intake"]["workspace"]["project_path"] == default_path
    assert captured["intake"]["workspace"]["allowed_read_paths"] == [default_path]
    assert captured["intake"]["workspace"]["allowed_write_paths"] == [default_path]


def test_dev_shelf_create_run_rejects_duplicate_project_slug(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    service = DevShelfReadService(root, tools_root=root)
    (root / "docs" / "hello_world").mkdir(parents=True)
    monkeypatch.setattr(
        service,
        "_run_dev_shelf_tool",
        lambda script_name, args: pytest.fail("duplicate project must not call dev-shelf start script"),
    )
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    with pytest.raises(HTTPException) as exc:
        routes.create_dev_shelf_run(
            DevShelfProjectCreateRequest(
                project_name="Hello World",
                requirement="重新创建同名 run",
            )
        )

    assert exc.value.status_code == 409
    assert "项目名已存在" in str(exc.value.detail)


def test_dev_shelf_directory_routes_list_and_create_project_folders(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    root.mkdir()
    existing = tmp_path / "existing-project"
    existing.mkdir()
    (tmp_path / "not-a-dir.txt").write_text("ignore", encoding="utf-8")
    service = DevShelfReadService(root, tools_root=root)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    listing = routes.list_dev_shelf_directories()
    created = routes.create_dev_shelf_directory(
        DevShelfDirectoryCreateRequest(parent_path=str(tmp_path), name="new-project")
    )
    created_listing = routes.list_dev_shelf_directories(created.path)

    assert listing.root_path == str(tmp_path.resolve())
    assert listing.current_path == str(tmp_path.resolve())
    assert any(item.name == "existing-project" for item in listing.items)
    assert created.path == str((tmp_path / "new-project").resolve())
    assert created_listing.parent_path == str(tmp_path.resolve())


def test_dev_shelf_directory_routes_reject_paths_outside_projects_root(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    root.mkdir()
    service = DevShelfReadService(root, tools_root=root)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    with pytest.raises(HTTPException) as exc:
        routes.list_dev_shelf_directories("/")

    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as create_exc:
        routes.create_dev_shelf_directory(DevShelfDirectoryCreateRequest(parent_path=str(tmp_path), name="../bad"))

    assert create_exc.value.status_code == 409


def test_dev_shelf_gateway_routes_expose_status_events_result_and_candidates(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir)
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    status = routes.get_dev_shelf_gateway_latest("run_demo_20260415000000")
    events = routes.get_dev_shelf_gateway_events(
        "run_demo_20260415000000",
        cursor=0,
        limit=1,
    )
    transcript = routes.get_dev_shelf_gateway_transcript("run_demo_20260415000000")
    result = routes.get_dev_shelf_gateway_result("run_demo_20260415000000")
    candidates = routes.get_dev_shelf_gateway_candidates("run_demo_20260415000000")

    assert status.gateway_session_id == "session-demo"
    assert status.status == "completed"
    assert status.provider == "openai-codex"
    assert status.model == "gpt-5.4"
    assert status.event_count == 3
    assert status.artifact_result_summary == {"output_count": 1, "produced_count": 1}
    assert events.event_count == 1
    assert events.next_cursor == 1
    assert events.has_more is True
    assert events.events[0]["kind"] == "response"
    assert transcript.message_count == 2
    assert transcript.messages[0].role == "tool"
    assert transcript.messages[1].role == "assistant"
    assert transcript.messages[1].text == "hello"
    assert result.payload is not None
    assert result.payload["summary"]["output_count"] == 1
    assert candidates.payload is not None
    assert candidates.payload["summary"]["candidate_count"] == 1
    assert candidates.payload["preview_artifacts"][0]["artifact_id"] == "requirement_confirmation_checklist"
    assert candidates.payload["preview_artifacts"][0]["title"] == "需求确认清单"
    assert candidates.payload["preview_artifacts"][0]["content"] == "# 需求确认清单\n\n请确认目标用户和页面范围。"
    assert candidates.payload["preview_artifacts"][0]["review_required"] is True


def test_dev_shelf_gateway_transcript_ignores_tool_result_message_snapshots(tmp_path) -> None:
    runtime_events_path = tmp_path / "runtime-events.jsonl"
    runtime_events_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "stream": "runtime",
                        "kind": "response",
                        "sequence": 1,
                        "raw": {"command": "read", "success": True},
                    }
                ),
                json.dumps(
                    {
                        "stream": "runtime",
                        "kind": "text",
                        "sequence": 2,
                        "raw": {
                            "type": "message_update",
                            "assistantMessageEvent": {
                                "type": "text_delta",
                                "delta": "import { pageContent } from './content/site.js';",
                                "partial": {"role": "toolResult"},
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "stream": "runtime",
                        "kind": "response",
                        "sequence": 3,
                        "raw": {
                            "type": "message_end",
                            "message": {
                                "role": "toolResult",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "import { pageContent } from './content/site.js';",
                                    }
                                ],
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "stream": "runtime",
                        "kind": "response",
                        "sequence": 4,
                        "raw": {
                            "type": "message_end",
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": "我会基于需求确认清单继续整理。"}],
                            },
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = DevShelfReadService(tmp_path / "dev-shelf")

    messages, event_count = service._read_gateway_transcript(runtime_events_path)

    assert event_count == 4
    assert [message.role for message in messages] == ["tool", "assistant"]
    assert messages[0].text == "read完成"
    assert messages[1].text == "我会基于需求确认清单继续整理。"
    assert all("pageContent" not in message.text for message in messages)


def test_dev_shelf_gateway_failed_session_hides_stale_candidate_previews(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir, status="failed")
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    candidates = routes.get_dev_shelf_gateway_candidates("run_demo_20260415000000")

    assert candidates.payload is not None
    assert candidates.payload["summary"]["candidate_count"] == 0
    assert candidates.payload["summary"]["review_required_candidate_count"] == 0
    assert candidates.payload["candidates"] == []
    assert candidates.payload["preview_artifacts"] == []
    assert candidates.payload["skipped_reason"] == "gateway_status_failed"


def test_dev_shelf_gateway_stream_sends_sse_events(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir)
    service = DevShelfReadService(root)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    response = routes.stream_dev_shelf_gateway_events(
        "run_demo_20260415000000",
        session_id="session-demo",
        cursor=1,
        limit=10,
        last_event_id=None,
    )
    body = collect_gateway_stream(
        service,
        "run_demo_20260415000000",
        session_id="session-demo",
        cursor=1,
        limit=10,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "retry: 3000" in body
    assert "event: assistant_delta" in body
    assert '"delta":"hello"' in body
    assert "event: file_write" in body
    assert "/tmp/demo.txt" in body


def test_dev_shelf_gateway_stream_resumes_from_last_event_id(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir)
    service = DevShelfReadService(root)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    response = routes.stream_dev_shelf_gateway_events(
        "run_demo_20260415000000",
        session_id="session-demo",
        last_event_id="session-demo:2:assistant_delta:0",
    )
    body = collect_gateway_stream(
        service,
        "run_demo_20260415000000",
        session_id="session-demo",
        last_event_id="session-demo:2:assistant_delta:0",
    )

    assert response.status_code == 200
    assert "event: assistant_delta" not in body
    assert "event: file_write" in body


def test_dev_shelf_gateway_candidate_confirm_applies_and_approves_artifact(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir)
    service = DevShelfReadService(root, tools_root=root)
    calls = []

    def fake_run_tool(script_name, args):
        calls.append((script_name, args))
        run_state_path = run_dir / "run-state.json"
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        if script_name == "dev_shelf_workflow_action.py":
            assert args[0] == "--pretty"
            assert args[1] == "apply-candidate"
            assert "--candidate-id" in args
            assert "candidate-0001-demo" in args
            state.setdefault("artifacts", []).append(
                {
                    "artifact_id": "requirement_confirmation_checklist",
                    "title": "需求确认清单",
                    "status": "draft",
                    "path": str(root / "docs" / "demo" / "requirement-confirmation.md"),
                }
            )
            write_json(run_state_path, state)
            return {"status": "applied"}
        if script_name == "dev_shelf_emit_event.py":
            if args[0] == "artifact":
                artifact_id = args[args.index("--artifact-id") + 1]
                artifact_status = args[args.index("--artifact-status") + 1]
                for artifact in state["artifacts"]:
                    if artifact.get("artifact_id") == artifact_id:
                        artifact["status"] = artifact_status
            elif args[0] == "stage":
                state["current_stage"] = args[args.index("--stage") + 1]
                state["status"] = args[args.index("--run-status") + 1]
                next_allowed_index = args.index("--next-allowed")
                state["next_allowed"] = args[next_allowed_index + 1 : next_allowed_index + 3]
            else:
                raise AssertionError(args)
            write_json(run_state_path, state)
            return {}
        if script_name == "dev_shelf_runner.py":
            write_json(
                run_dir / "packets" / "0002-execution-packet.json",
                {
                    "packet_version": "1.0",
                    "decision_type": "run_manifest",
                    "ready": True,
                    "target": "template.spec",
                },
            )
            return {}
        raise AssertionError(script_name)

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    detail = routes.confirm_dev_shelf_gateway_candidate(
        "run_demo_20260415000000",
        "candidate-0001-demo",
        DevShelfGatewayCandidateConfirmRequest(session_id="session-demo"),
    )

    assert [call[0] for call in calls] == [
        "dev_shelf_workflow_action.py",
        "dev_shelf_emit_event.py",
        "dev_shelf_emit_event.py",
        "dev_shelf_runner.py",
    ]
    artifact = next(item for item in detail.artifacts if item.artifact_id == "requirement_confirmation_checklist")
    assert artifact.status == "approved"
    assert detail.current_stage == "confirmed_requirement"
    assert detail.status == "ready_for_next_stage"
    assert detail.latest_packet is not None
    assert detail.latest_packet.target == "template.spec"


def test_dev_shelf_gateway_candidate_revise_rejects_and_writes_feedback(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir)
    service = DevShelfReadService(root, tools_root=root)
    calls = []

    def fake_run_tool(script_name, args):
        calls.append((script_name, args))
        run_state_path = run_dir / "run-state.json"
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        if script_name == "dev_shelf_workflow_action.py":
            assert args[1] == "apply-candidate"
            state.setdefault("artifacts", []).append(
                {
                    "artifact_id": "requirement_confirmation_checklist",
                    "title": "需求确认清单",
                    "status": "draft",
                    "path": str(root / "docs" / "demo" / "requirement-confirmation.md"),
                }
            )
            write_json(run_state_path, state)
            return {"status": "applied"}
        if script_name == "dev_shelf_emit_event.py":
            assert args[0] == "artifact"
            artifact_id = args[args.index("--artifact-id") + 1]
            artifact_status = args[args.index("--artifact-status") + 1]
            note = args[args.index("--note") + 1]
            assert "需要补充中止和重新生成入口" in note
            for artifact in state["artifacts"]:
                if artifact.get("artifact_id") == artifact_id:
                    artifact["status"] = artifact_status
            write_json(run_state_path, state)
            return {}
        if script_name == "dev_shelf_runner.py":
            write_json(
                run_dir / "packets" / "0002-execution-packet.json",
                {
                    "packet_version": "1.0",
                    "decision_type": "run_manifest",
                    "ready": True,
                    "target": "template.requirement-confirmation-checklist",
                },
            )
            return {}
        raise AssertionError(script_name)

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    detail = routes.revise_dev_shelf_gateway_candidate(
        "run_demo_20260415000000",
        "candidate-0001-demo",
        DevShelfGatewayCandidateReviseRequest(
            session_id="session-demo",
            feedback="需要补充中止和重新生成入口。",
        ),
    )

    assert [call[0] for call in calls] == [
        "dev_shelf_workflow_action.py",
        "dev_shelf_emit_event.py",
        "dev_shelf_runner.py",
    ]
    artifact = next(item for item in detail.artifacts if item.artifact_id == "requirement_confirmation_checklist")
    assert artifact.status == "rejected"
    feedback_files = list((run_dir / "artifacts" / "workbench-feedback").glob("*.md"))
    assert len(feedback_files) == 1
    assert "需要补充中止和重新生成入口" in feedback_files[0].read_text(encoding="utf-8")
    assert detail.latest_packet is not None
    assert detail.latest_packet.decision_type == "run_manifest"


def test_dev_shelf_gateway_register_result_writes_artifact_and_advances(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir, implementation_path = make_execution_registerable_run(root)
    service = DevShelfReadService(root, tools_root=root)
    calls = []

    def fake_run_tool(script_name, args):
        calls.append((script_name, args))
        run_state_path = run_dir / "run-state.json"
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        if script_name == "dev_shelf_emit_event.py":
            assert args[0] == "artifact"
            assert args[args.index("--artifact-id") + 1] == "implementation_result"
            assert args[args.index("--artifact-status") + 1] == "done"
            state.setdefault("artifacts", []).append(
                {
                    "artifact_id": "implementation_result",
                    "title": args[args.index("--title") + 1],
                    "status": "done",
                    "path": args[args.index("--path") + 1],
                    "produced_by": args[args.index("--produced-by") + 1],
                }
            )
            write_json(run_state_path, state)
            return {}
        if script_name == "dev_shelf_runner.py":
            write_json(
                run_dir / "packets" / "0002-execution-packet.json",
                {
                    "packet_version": "1.0",
                    "decision_type": "enter_stage",
                    "ready": True,
                    "target": "stage.review",
                },
            )
            return {}
        raise AssertionError(script_name)

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    detail = routes.register_dev_shelf_gateway_result(
        "run_demo_20260425000000",
        DevShelfGatewayRegisterResultRequest(session_id="session-demo"),
    )

    assert [call[0] for call in calls] == ["dev_shelf_emit_event.py", "dev_shelf_runner.py"]
    content = implementation_path.read_text(encoding="utf-8")
    assert "Successfully wrote 10 bytes" in content
    assert "Implemented the static page." in content
    artifact = next(item for item in detail.artifacts if item.artifact_id == "implementation_result")
    assert artifact.status == "done"
    assert artifact.path == str(implementation_path)
    assert detail.latest_packet is not None
    assert detail.latest_packet.target == "stage.review"


def test_dev_shelf_gateway_register_result_rejects_stale_non_execution_session(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir, _implementation_path = make_execution_registerable_run(root)
    service = DevShelfReadService(root, tools_root=root)
    latest_packet = run_dir / "packets" / "0002-execution-packet.json"
    write_json(
        latest_packet,
        {
            "packet_version": "1.0",
            "decision_type": "enter_stage",
            "ready": True,
            "target": "stage.execution",
            "stage": "execution",
        },
    )
    metadata_path = run_dir / "artifacts" / "pi-agent-gateway" / "session-demo" / "session-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["packet_path"] = str(run_dir / "packets" / "0001-execution-packet.json")
    metadata["packet_target"] = "template.execution-todo"
    write_json(metadata_path, metadata)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    with pytest.raises(HTTPException) as exc:
        routes.register_dev_shelf_gateway_result(
            "run_demo_20260425000000",
            DevShelfGatewayRegisterResultRequest(session_id="session-demo"),
        )

    assert exc.value.status_code == 409
    assert "不是 execution 阶段结果" in str(exc.value.detail)


def test_dev_shelf_gateway_candidate_confirm_advances_followup_stages(monkeypatch, tmp_path) -> None:
    service = DevShelfReadService(tmp_path, tools_root=tmp_path)
    run_state_path = tmp_path / "runs" / "run_demo" / "run-state.json"
    calls = []

    def fake_run_tool(script_name, args):
        calls.append((script_name, args))
        return {}

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)

    expected = [
        ("implementation_plan", "spec_drafting", "in_progress"),
        ("spec", "reuse_decision", "in_progress"),
        ("reuse_decision", "implementation_planning", "in_progress"),
    ]
    for artifact_id, _stage, _status in expected:
        service._advance_after_confirmed_gateway_candidate(
            run_state_path=run_state_path,
            artifact_id=artifact_id,
        )

    assert len(calls) == len(expected)
    for (script_name, args), (artifact_id, expected_stage, expected_status) in zip(calls, expected, strict=True):
        assert script_name == "dev_shelf_emit_event.py"
        assert args[0] == "stage"
        assert args[args.index("--stage") + 1] == expected_stage
        assert args[args.index("--run-status") + 1] == expected_status
        assert "--next-allowed" not in args
        assert artifact_id in {
            "implementation_plan",
            "spec",
            "reuse_decision",
        }


def test_dev_shelf_cancel_run_marks_state_and_stops_active_gateway(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    service = DevShelfReadService(root, tools_root=root)
    fake_process = FakeGatewayProcess()
    service._gateway_launches["run_demo_20260415000000"] = DevShelfGatewayLaunch(
        run_id="run_demo_20260415000000",
        process=fake_process,
        launch_id="launch-test",
        started_at="2026-04-24T00:00:00Z",
        log_path=run_dir / "launch.log",
        command=["gateway"],
    )
    captured = {}

    def fake_run_tool(script_name, args):
        captured["script_name"] = script_name
        captured["args"] = args
        run_state_path = Path(args[args.index("--run-state") + 1])
        state = json.loads(run_state_path.read_text(encoding="utf-8"))
        state["status"] = "cancelled"
        state.setdefault("history", []).append(
            {
                "from_stage": state.get("current_stage"),
                "to_stage": state.get("current_stage"),
                "action": "enter",
                "actor": "human",
                "reason": "用户在网页终止任务。",
                "at": "2026-04-24T00:00:00Z",
            }
        )
        write_json(run_state_path, state)
        return {}

    monkeypatch.setattr(service, "_run_dev_shelf_tool", fake_run_tool)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    detail = routes.cancel_dev_shelf_run(
        "run_demo_20260415000000",
        DevShelfRunCancelRequest(note="用户在网页终止任务。"),
    )

    assert captured["script_name"] == "dev_shelf_emit_event.py"
    assert captured["args"][0] == "stage"
    assert "--run-status" in captured["args"]
    assert "cancelled" in captured["args"]
    assert detail.status == "cancelled"
    assert signal.SIGINT in fake_process.signals
    assert service._gateway_launches == {}


def test_dev_shelf_gateway_routes_reject_unknown_session(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    make_gateway_session(run_dir)
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    with pytest.raises(HTTPException) as exc:
        routes.get_dev_shelf_gateway_events(
            "run_demo_20260415000000",
            session_id="session-missing",
        )

    assert exc.value.status_code == 404


def test_dev_shelf_gateway_start_and_abort_routes(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    make_gateway_runnable_run(root)
    script_path = root / "scripts" / "dev_shelf_gateway.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    service = DevShelfReadService(root, tools_root=root)
    fake_process = FakeGatewayProcess()
    captured = {}

    def fake_spawn(command, log_path):
        captured["command"] = command
        captured["log_path"] = log_path
        return fake_process

    monkeypatch.setattr(service, "_spawn_gateway_process", fake_spawn)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    started = routes.start_dev_shelf_gateway(
        "run_demo_20260415000000",
        DevShelfGatewayStartRequest(account="c", model="gpt-5.4", light_mode=True),
    )
    aborted = routes.abort_dev_shelf_gateway(
        "run_demo_20260415000000",
        DevShelfGatewayAbortRequest(),
    )

    assert started.status == "started"
    assert started.pid == 4321
    assert "--account" in captured["command"]
    assert "c" in captured["command"]
    assert "--model" in captured["command"]
    assert "gpt-5.4" in captured["command"]
    assert "--pi-arg=--no-tools" in captured["command"]
    assert "--pi-arg=--no-context-files" in captured["command"]
    assert str(captured["log_path"]).endswith(".log")
    assert aborted.status == "aborted"
    assert signal.SIGINT in fake_process.signals


def test_dev_shelf_model_config_roundtrip(monkeypatch, tmp_path) -> None:
    service = DevShelfReadService(tmp_path / "dev-shelf", tools_root=tmp_path / "dev-shelf")
    monkeypatch.setattr(service, "_gateway_accounts", lambda: ["a", "b"])
    monkeypatch.setattr(service, "_load_pi_settings", lambda: {})
    monkeypatch.setattr(service, "_pi_auth_configured", lambda provider: provider == "deepseek")
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    initial = routes.get_dev_shelf_model_config()
    updated = routes.update_dev_shelf_model_config(
        DevShelfModelConfigUpdateRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
        )
    )
    stored = json.loads(service.model_config_path.read_text(encoding="utf-8"))

    assert initial.provider == "openai-codex"
    assert updated.provider == "deepseek"
    assert updated.model == "deepseek-v4-pro"
    assert updated.account is None
    assert next(item for item in updated.providers if item.provider == "deepseek").auth_configured
    assert "api_keys" not in stored


def test_dev_shelf_gateway_start_deepseek_uses_pi_auth_without_workbench_secret(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    make_gateway_runnable_run(root)
    script_path = root / "scripts" / "dev_shelf_gateway.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    service = DevShelfReadService(root, tools_root=root)
    service.update_model_config(
        DevShelfModelConfigUpdateRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
        )
    )
    fake_process = FakeGatewayProcess()
    captured = {}

    def fake_spawn(command, log_path, env=None):
        captured["command"] = command
        captured["env"] = env
        return fake_process

    monkeypatch.setattr(service, "_spawn_gateway_process", fake_spawn)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    started = routes.start_dev_shelf_gateway(
        "run_demo_20260415000000",
        DevShelfGatewayStartRequest(
            account=None,
            provider="deepseek",
            model="deepseek-v4-pro",
        ),
    )

    assert started.status == "started"
    assert "--provider" in captured["command"]
    assert "deepseek" in captured["command"]
    assert "--model" in captured["command"]
    assert "deepseek-v4-pro" in captured["command"]
    assert "--api-key" not in captured["command"]
    assert captured["env"] is None


def test_dev_shelf_gateway_start_rejects_enter_stage_packet(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_id = "run_demo_20260426000000"
    run_dir = root / "runs" / run_id
    project_path = root / "project"
    project_path.mkdir(parents=True)
    write_json(
        run_dir / "run-state.json",
        {
            "run_id": run_id,
            "project_name": "Demo Run",
            "request_summary": "Do not start pi-agent for enter_stage.",
            "current_stage": "implementation_planning",
            "status": "ready_for_next_stage",
            "artifacts": [],
            "history": [],
        },
    )
    write_json(
        run_dir / "packets" / "0001-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "enter_stage",
            "ready": True,
            "target": "stage.execution",
            "workspace": {"project_path": str(project_path)},
            "agent_runtime_contract": {"cwd": str(project_path)},
        },
    )
    service = DevShelfReadService(root, tools_root=root)
    monkeypatch.setattr(
        service,
        "_spawn_gateway_process",
        lambda command, log_path: pytest.fail("enter_stage must not spawn pi-agent"),
    )
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    with pytest.raises(HTTPException) as exc:
        routes.start_dev_shelf_gateway(run_id, DevShelfGatewayStartRequest())

    assert exc.value.status_code == 409
    assert "不是生成任务" in exc.value.detail


def test_dev_shelf_gateway_start_rejects_completed_current_packet(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_gateway_runnable_run(root)
    make_gateway_session(run_dir)
    service = DevShelfReadService(root, tools_root=root)
    monkeypatch.setattr(
        service,
        "_spawn_gateway_process",
        lambda command, log_path: pytest.fail("completed packet must not spawn pi-agent"),
    )
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    with pytest.raises(HTTPException) as exc:
        routes.start_dev_shelf_gateway("run_demo_20260415000000", DevShelfGatewayStartRequest())

    assert exc.value.status_code == 409
    assert "已完成执行" in exc.value.detail


def test_dev_shelf_gateway_start_rejects_running_process(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    make_gateway_runnable_run(root)
    script_path = root / "scripts" / "dev_shelf_gateway.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    service = DevShelfReadService(root, tools_root=root)
    monkeypatch.setattr(service, "_spawn_gateway_process", lambda command, log_path: FakeGatewayProcess())
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    routes.start_dev_shelf_gateway("run_demo_20260415000000", DevShelfGatewayStartRequest())
    with pytest.raises(HTTPException) as exc:
        routes.start_dev_shelf_gateway("run_demo_20260415000000", DevShelfGatewayStartRequest())

    assert exc.value.status_code == 409


def test_dev_shelf_gateway_latest_reports_failed_launch_log(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_gateway_runnable_run(root)
    make_gateway_session(run_dir, session_id="session-old", packet_path=run_dir / "packets" / "0000-execution-packet.json")
    script_path = root / "scripts" / "dev_shelf_gateway.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    service = DevShelfReadService(root, tools_root=root)
    fake_process = FakeGatewayProcess()

    def fake_spawn(command, log_path):
        log_path.write_text("gateway failed before session\n", encoding="utf-8")
        return fake_process

    monkeypatch.setattr(service, "_spawn_gateway_process", fake_spawn)

    started = service.start_gateway("run_demo_20260415000000", DevShelfGatewayStartRequest())
    fake_process.returncode = 1
    status = service.get_gateway_status("run_demo_20260415000000")

    assert started.status == "started"
    assert status.status == "failed"
    assert status.gateway_session_id is None
    assert "gateway failed before session" in (status.error or "")


def test_dev_shelf_gateway_start_rejects_run_without_workspace(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = root / "runs" / "run_demo_20260418000000"
    write_json(
        run_dir / "run-state.json",
        {
            "run_id": "run_demo_20260418000000",
            "project_name": "Demo Run",
            "request_summary": "Missing workspace.",
            "current_stage": "requirements_drafting",
            "status": "in_progress",
            "artifacts": [],
            "history": [],
        },
    )
    write_json(
        run_dir / "packets" / "0001-execution-packet.json",
        {
            "packet_version": "1.0",
            "decision_type": "run_manifest",
            "ready": True,
            "target": "template.requirement-confirmation-checklist",
            "pending_outputs": [
                {
                    "artifact_id": "requirement_confirmation_checklist",
                    "title": "需求确认清单",
                }
            ],
            "workspace": {"project_path": None},
            "agent_runtime_contract": {"cwd": None},
        },
    )
    service = DevShelfReadService(root, tools_root=root)
    monkeypatch.setattr(routes, "dev_shelf_service", service)

    with pytest.raises(HTTPException) as exc:
        routes.start_dev_shelf_gateway("run_demo_20260418000000", DevShelfGatewayStartRequest())

    assert exc.value.status_code == 409
    assert "没有项目路径" in exc.value.detail


def test_dev_shelf_run_detail_rejects_artifact_preview_outside_root(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = root / "runs" / "run_demo_20260416000000"
    outside = tmp_path / "outside.md"
    outside.write_text("do not expose", encoding="utf-8")
    write_json(
        run_dir / "run-state.json",
        {
            "run_id": "run_demo_20260416000000",
            "project_name": "Demo Run",
            "request_summary": "Do not preview arbitrary files.",
            "task_type": "feature",
            "task_type_status": "confirmed",
            "current_stage": "spec_confirmation",
            "status": "awaiting_human",
            "artifacts": [
                {
                    "artifact_id": "spec",
                    "title": "项目 spec",
                    "status": "approved",
                    "path": str(outside),
                    "produced_by": "template.spec",
                }
            ],
            "history": [],
        },
    )
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    detail = routes.get_dev_shelf_run("run_demo_20260416000000")

    assert detail.artifacts[0].content is None
    assert detail.artifacts[0].content_format == "unsupported"
    assert detail.artifacts[0].content_error == "产物路径不在 dev-shelf 根目录内，已拒绝预览。"


def test_dev_shelf_run_detail_truncates_large_artifact_preview(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = root / "runs" / "run_demo_20260417000000"
    artifact_path = root / "docs" / "demo" / "large.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("a" * (ARTIFACT_PREVIEW_LIMIT + 10), encoding="utf-8")
    write_json(
        run_dir / "run-state.json",
        {
            "run_id": "run_demo_20260417000000",
            "project_name": "Demo Run",
            "request_summary": "Preview large files safely.",
            "task_type": "feature",
            "task_type_status": "confirmed",
            "current_stage": "spec_confirmation",
            "status": "awaiting_human",
            "artifacts": [
                {
                    "artifact_id": "spec",
                    "title": "项目 spec",
                    "status": "approved",
                    "path": "docs/demo/large.md",
                    "produced_by": "template.spec",
                }
            ],
            "history": [],
        },
    )
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    detail = routes.get_dev_shelf_run("run_demo_20260417000000")

    assert detail.artifacts[0].content == "a" * ARTIFACT_PREVIEW_LIMIT
    assert detail.artifacts[0].content_truncated is True
    assert detail.artifacts[0].content_error is None


def test_dev_shelf_gate_decision_approves_artifact_and_writes_packet(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    detail = routes.decide_dev_shelf_human_gate(
        "run_demo_20260415000000",
        "spec_approval",
        DevShelfHumanGateDecisionRequest(decision="approved", decision_note="spec approved"),
    )

    state_after = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    spec = next(item for item in state_after["artifacts"] if item["artifact_id"] == "spec")
    gate = next(item for item in state_after["human_gates"] if item["gate_id"] == "spec_approval")

    assert spec["status"] == "approved"
    assert gate["status"] == "approved"
    assert detail.pending_human_gates == []
    assert detail.latest_packet is not None
    assert detail.latest_packet.sequence == 2
    assert len(list((run_dir / "events").glob("*spec-approved.json"))) == 1


def test_dev_shelf_gate_decision_rejects_and_blocks_run(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    detail = routes.decide_dev_shelf_human_gate(
        "run_demo_20260415000000",
        "spec_approval",
        DevShelfHumanGateDecisionRequest(decision="rejected", decision_note="needs revision"),
    )

    state_after = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    spec = next(item for item in state_after["artifacts"] if item["artifact_id"] == "spec")
    gate = next(item for item in state_after["human_gates"] if item["gate_id"] == "spec_approval")

    assert spec["status"] == "rejected"
    assert gate["status"] == "rejected"
    assert state_after["status"] == "blocked"
    assert detail.status == "blocked"


def test_dev_shelf_gate_decision_rejects_non_pending_gate(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    run_dir = make_pending_gate_run(root)
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    state["status"] = "ready_for_next_stage"
    state["artifacts"][0]["status"] = "approved"
    state["human_gates"][0]["status"] = "approved"
    write_json(run_dir / "run-state.json", state)
    monkeypatch.setattr(routes, "dev_shelf_service", DevShelfReadService(root))

    with pytest.raises(HTTPException) as exc:
        routes.decide_dev_shelf_human_gate(
            "run_demo_20260415000000",
            "spec_approval",
            DevShelfHumanGateDecisionRequest(decision="approved"),
        )

    assert exc.value.status_code == 409
