import json
import signal

import pytest
from fastapi import HTTPException

from app.api import routes
from app.schemas.dev_shelf import (
    DevShelfGatewayAbortRequest,
    DevShelfGatewayStartRequest,
    DevShelfHumanGateDecisionRequest,
    DevShelfProjectCreateRequest,
)
from app.services.dev_shelf import ARTIFACT_PREVIEW_LIMIT, DevShelfReadService


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


def make_gateway_session(run_dir, session_id="session-demo") -> None:
    session_dir = run_dir / "artifacts" / "pi-agent-gateway" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    runtime_events_path = session_dir / "runtime-events.jsonl"
    gateway_result_path = session_dir / "gateway-result.json"
    candidates_path = session_dir / "gateway-event-candidates.json"
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
                        "raw": {"delta": "hello"},
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
            "run_id": run_dir.name,
            "gateway_session_id": session_id,
            "summary": {"candidate_count": 1, "skipped_count": 0},
            "candidates": [{"candidate_id": "candidate-0001-demo"}],
            "skipped": [],
        },
    )
    write_json(
        session_dir / "session-metadata.json",
        {
            "runtime_event_schema_version": "1.0",
            "event_count": 2,
            "status": "completed",
            "started_at": "2026-04-24T00:00:00Z",
            "finished_at": "2026-04-24T00:00:10Z",
            "run_id": run_dir.name,
            "gateway_session_id": session_id,
            "provider": "openai-codex",
            "model": "gpt-5.4",
            "pi_account": "a",
            "pi_session_id": "pi-demo",
            "runtime_events_path": str(runtime_events_path),
            "gateway_result_json": str(gateway_result_path),
            "gateway_event_candidates_json": str(candidates_path),
            "artifact_result_summary": {"output_count": 1, "produced_count": 1},
            "event_candidate_summary": {"candidate_count": 1, "skipped_count": 0},
        },
    )


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
    result = routes.get_dev_shelf_gateway_result("run_demo_20260415000000")
    candidates = routes.get_dev_shelf_gateway_candidates("run_demo_20260415000000")

    assert status.gateway_session_id == "session-demo"
    assert status.status == "completed"
    assert status.provider == "openai-codex"
    assert status.model == "gpt-5.4"
    assert status.event_count == 2
    assert status.artifact_result_summary == {"output_count": 1, "produced_count": 1}
    assert events.event_count == 1
    assert events.next_cursor == 1
    assert events.has_more is True
    assert events.events[0]["kind"] == "response"
    assert result.payload is not None
    assert result.payload["summary"]["output_count"] == 1
    assert candidates.payload is not None
    assert candidates.payload["summary"]["candidate_count"] == 1


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
    make_pending_gate_run(root)
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


def test_dev_shelf_gateway_start_rejects_running_process(monkeypatch, tmp_path) -> None:
    root = tmp_path / "dev-shelf"
    make_pending_gate_run(root)
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
