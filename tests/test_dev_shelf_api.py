import json

import pytest
from fastapi import HTTPException

from app.api import routes
from app.schemas.dev_shelf import DevShelfHumanGateDecisionRequest
from app.services.dev_shelf import ARTIFACT_PREVIEW_LIMIT, DevShelfReadService


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def make_pending_gate_run(root, run_id="run_demo_20260415000000"):
    run_dir = root / "runs" / run_id
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
        },
    )
    return run_dir


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
