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


class DevShelfDirectoryEntry(APIModel):
    name: str
    path: str


class DevShelfDirectoryList(APIModel):
    root_path: str
    current_path: str
    parent_path: str | None = None
    items: list[DevShelfDirectoryEntry] = Field(default_factory=list)


class DevShelfDirectoryCreateRequest(APIModel):
    parent_path: str | None = None
    name: str = Field(min_length=1)


class DevShelfDirectoryCreateResponse(APIModel):
    path: str


class DevShelfProjectCreateRequest(APIModel):
    project_name: str = Field(min_length=1)
    requirement: str = Field(min_length=1)
    request_summary: str | None = None
    project_slug: str | None = None
    task_type: Literal[
        "new-project",
        "feature",
        "bugfix",
        "refactor",
        "automation",
        "analysis",
        "structured-llm-app",
        "general",
    ] = "general"
    task_type_status: Literal["tentative", "confirmed"] = "tentative"
    project_context: Literal["new_project", "existing_project", "unknown"] = "new_project"
    project_path: str | None = None
    allow_create_project_dir: bool = False
    workspace_confirmed: bool = False


class DevShelfProjectCreateResponse(APIModel):
    schema_version: str = "1.0"
    status: str
    project_name: str
    project_slug: str
    run_id: str
    docs_dir: str | None = None
    run_dir: str | None = None
    requirement_draft: str | None = None
    run_state: str | None = None
    first_execution_packet_json: str | None = None
    first_execution_packet_markdown: str | None = None
    effective_task_type: str | None = None
    suggested_task_type: str | None = None
    task_type_status: str | None = None
    project_context: str | None = None
    requires_existing_project_analysis: bool = False
    next_decision_type: str | None = None
    next_target: str | list[str] | None = None
    message: str | None = None


class DevShelfRunDetail(DevShelfRunSummary):
    task_type_status: str | None = None
    artifacts: list[DevShelfArtifact]
    pending_human_gates: list[DevShelfHumanGate] = Field(default_factory=list)
    router: DevShelfRouterResult | None = None
    latest_packet: DevShelfExecutionPacket | None = None


class DevShelfHumanGateDecisionRequest(APIModel):
    decision: Literal["approved", "rejected"]
    decision_note: str | None = None


class DevShelfArtifactReviseRequest(APIModel):
    feedback: str = Field(min_length=1)


class DevShelfRunCancelRequest(APIModel):
    note: str | None = None


class DevShelfGatewayCandidateConfirmRequest(APIModel):
    session_id: str | None = None
    decision_note: str | None = None


class DevShelfGatewayCandidateReviseRequest(APIModel):
    session_id: str | None = None
    feedback: str = Field(min_length=1)


class DevShelfGatewayRegisterResultRequest(APIModel):
    session_id: str | None = None
    note: str | None = None


class DevShelfGatewaySessionStatus(APIModel):
    schema_version: str = "1.0"
    run_id: str | None = None
    gateway_session_id: str | None = None
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    pi_account: str | None = None
    pi_account_dir: str | None = None
    pi_session_id: str | None = None
    packet_path: str | None = None
    packet_target: str | None = None
    session_dir: str | None = None
    metadata_path: str | None = None
    runtime_events_path: str | None = None
    runtime_event_schema_version: str | None = None
    event_count: int = 0
    gateway_result_json: str | None = None
    gateway_result_markdown: str | None = None
    gateway_event_candidates_json: str | None = None
    gateway_event_candidates_markdown: str | None = None
    artifact_result_summary: dict[str, Any] | None = None
    event_candidate_summary: dict[str, Any] | None = None
    abort_requested: bool = False
    error: str | None = None


class DevShelfGatewayRuntimeEvents(APIModel):
    schema_version: str = "1.0"
    run_id: str | None = None
    gateway_session_id: str | None = None
    session_dir: str | None = None
    runtime_events_path: str | None = None
    cursor: int = 0
    next_cursor: int = 0
    limit: int = 100
    has_more: bool = False
    event_count: int = 0
    total_seen: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)


class DevShelfGatewayTranscriptMessage(APIModel):
    role: Literal["assistant", "tool", "system", "error"]
    kind: str
    text: str
    sequence_start: int | None = None
    sequence_end: int | None = None
    ts: str | None = None


class DevShelfGatewayTranscript(APIModel):
    schema_version: str = "1.0"
    run_id: str | None = None
    gateway_session_id: str | None = None
    session_dir: str | None = None
    runtime_events_path: str | None = None
    message_count: int = 0
    event_count: int = 0
    messages: list[DevShelfGatewayTranscriptMessage] = Field(default_factory=list)


class DevShelfGatewayArtifactPayload(APIModel):
    schema_version: str = "1.0"
    run_id: str | None = None
    gateway_session_id: str | None = None
    path: str | None = None
    payload: dict[str, Any] | None = None


class DevShelfGatewayStartRequest(APIModel):
    account: str | None = "a"
    provider: str = "openai-codex"
    model: str = "gpt-5.4"
    thinking: str | None = None
    no_session: bool = True
    light_mode: bool = False
    request_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 0.5
    post_prompt_grace_seconds: float = 2.0


class DevShelfGatewayAbortRequest(APIModel):
    gateway_session_id: str | None = None


class DevShelfGatewayControlResponse(APIModel):
    schema_version: str = "1.0"
    run_id: str
    status: str
    pid: int | None = None
    returncode: int | None = None
    launch_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    log_path: str | None = None
    command: list[str] = Field(default_factory=list)
    gateway_session_id: str | None = None
    message: str | None = None


class DevShelfModelItem(APIModel):
    provider: str
    model: str
    context_window: str = "-"
    max_output: str = "-"
    thinking: str = "-"
    images: str = "-"


class DevShelfModelList(APIModel):
    models: list[DevShelfModelItem] = Field(default_factory=list)


class DevShelfModelProvider(APIModel):
    provider: str
    label: str
    requires_account: bool = False
    auth_configured: bool = False
    auth_source: str | None = None
    default_model: str | None = None
    default_account: str | None = None
    accounts: list[str] = Field(default_factory=list)


class DevShelfModelConfig(APIModel):
    provider: str = "openai-codex"
    model: str = "gpt-5.4"
    account: str | None = "a"
    providers: list[DevShelfModelProvider] = Field(default_factory=list)


class DevShelfModelConfigUpdateRequest(APIModel):
    provider: str = "openai-codex"
    model: str | None = None
    account: str | None = None
