from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.common import APIModel


class DevShelfArtifact(APIModel):
    artifact_id: str
    title: str
    status: str
    path: str | None = None
    produced_by: str | None = None
    updated_at: str | None = None
    content: str | None = None
    content_format: str | None = None
    content_truncated: bool = False
    content_error: str | None = None


class DevShelfExecutionPacket(APIModel):
    sequence: int | None = None
    path: str | None = None
    markdown_path: str | None = None
    decision_type: str | None = None
    target: str | list[str] | None = None
    ready: bool | None = None
    content: dict[str, Any] | None = None
    markdown: str | None = None


class DevShelfHumanGate(APIModel):
    gate_id: str
    label: str | None = None
    required_for_stage: str | None = None
    owner: str | None = None
    status: str | None = None
    decision_note: str | None = None
    artifact_id: str | None = None


class DevShelfRouterResult(APIModel):
    decision_type: str | None = None
    target: str | list[str] | None = None
    reason: str | None = None
    pending_human_gates: list[DevShelfHumanGate] = Field(default_factory=list)
    next_step_after_approval: str | None = None
    content: dict[str, Any] | None = None


class DevShelfRunSummary(APIModel):
    run_id: str
    project_name: str | None = None
    request_summary: str | None = None
    current_stage: str | None = None
    status: str | None = None
    task_type: str | None = None
    updated_at: str | None = None
    artifact_count: int = 0


class DevShelfRunList(APIModel):
    items: list[DevShelfRunSummary]


class DevShelfRunDetail(DevShelfRunSummary):
    task_type_status: str | None = None
    artifacts: list[DevShelfArtifact]
    pending_human_gates: list[DevShelfHumanGate] = Field(default_factory=list)
    router: DevShelfRouterResult | None = None
    latest_packet: DevShelfExecutionPacket | None = None


class DevShelfHumanGateDecisionRequest(APIModel):
    decision: Literal["approved", "rejected"]
    decision_note: str | None = None
