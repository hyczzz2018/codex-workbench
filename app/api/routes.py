from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.schemas.common import Event, EventType
from app.schemas.dev_shelf import (
    DevShelfArtifactReviseRequest,
    DevShelfDirectoryCreateRequest,
    DevShelfDirectoryCreateResponse,
    DevShelfDirectoryList,
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
    DevShelfHumanGateDecisionRequest,
    DevShelfModelConfig,
    DevShelfModelConfigUpdateRequest,
    DevShelfModelList,
    DevShelfProjectCreateRequest,
    DevShelfProjectCreateResponse,
    DevShelfRunCancelRequest,
    DevShelfRunDetail,
    DevShelfRunList,
)
from app.schemas.session import AcceptedResponse, ConfirmRequest, MessageCreate, MessageList, SessionCreate, SessionRead
from app.services.dev_shelf import (
    DevShelfGateConflict,
    DevShelfGatewayConflict,
    DevShelfProjectConflict,
    DevShelfRunNotFound,
    DevShelfToolError,
    DevShelfWorkflowConflict,
    dev_shelf_service,
)
from app.services.events import event_bus
from app.services.store import store
from app.services.workflow import workflow_service

router = APIRouter()


def _to_session_read(session_id: str) -> SessionRead:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionRead.model_validate(session.model_dump())


@router.post("/api/sessions", response_model=SessionRead)
def create_session(payload: SessionCreate) -> SessionRead:
    session = store.create_session(payload)
    workflow_service.bootstrap_session(session)
    return _to_session_read(session.id)


@router.get("/api/sessions/{session_id}", response_model=SessionRead)
def get_session(session_id: str) -> SessionRead:
    return _to_session_read(session_id)


@router.get("/api/sessions/{session_id}/messages", response_model=MessageList)
def list_messages(session_id: str) -> MessageList:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return MessageList(items=store.list_messages(session_id))


@router.get("/api/sessions/{session_id}/artifact")
def get_artifact(session_id: str) -> dict:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    artifact = store.get_artifact(session_id)
    return artifact.model_dump(mode="json")


@router.get("/api/dev-shelf/runs", response_model=DevShelfRunList)
def list_dev_shelf_runs() -> DevShelfRunList:
    return DevShelfRunList(items=dev_shelf_service.list_runs())


@router.get("/api/dev-shelf/directories", response_model=DevShelfDirectoryList)
def list_dev_shelf_directories(path: str | None = None) -> DevShelfDirectoryList:
    try:
        return dev_shelf_service.list_project_directories(path)
    except DevShelfProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/dev-shelf/directories", response_model=DevShelfDirectoryCreateResponse)
def create_dev_shelf_directory(
    payload: DevShelfDirectoryCreateRequest,
) -> DevShelfDirectoryCreateResponse:
    try:
        return dev_shelf_service.create_project_directory(payload)
    except DevShelfProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs", response_model=DevShelfProjectCreateResponse)
def create_dev_shelf_run(payload: DevShelfProjectCreateRequest) -> DevShelfProjectCreateResponse:
    try:
        return dev_shelf_service.create_project(payload)
    except DevShelfProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/dev-shelf/runs/{run_id}", response_model=DevShelfRunDetail)
def get_dev_shelf_run(run_id: str) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.get_run(run_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/cancel", response_model=DevShelfRunDetail)
def cancel_dev_shelf_run(
    run_id: str,
    payload: DevShelfRunCancelRequest,
) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.cancel_run(run_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/workflow/continue", response_model=DevShelfRunDetail)
def continue_dev_shelf_workflow(run_id: str) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.continue_workflow(run_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfWorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/dev-shelf/runs/{run_id}/gateway/latest", response_model=DevShelfGatewaySessionStatus)
def get_dev_shelf_gateway_latest(run_id: str) -> DevShelfGatewaySessionStatus:
    try:
        return dev_shelf_service.get_gateway_status(run_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/dev-shelf/runs/{run_id}/gateway/events", response_model=DevShelfGatewayRuntimeEvents)
def get_dev_shelf_gateway_events(
    run_id: str,
    session_id: str | None = None,
    cursor: int = 0,
    limit: int = 100,
) -> DevShelfGatewayRuntimeEvents:
    try:
        return dev_shelf_service.get_gateway_events(
            run_id,
            session_id=session_id,
            cursor=cursor,
            limit=limit,
        )
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/dev-shelf/runs/{run_id}/gateway/stream")
def stream_dev_shelf_gateway_events(
    run_id: str,
    session_id: str | None = None,
    cursor: int = 0,
    limit: int = 100,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        stream = dev_shelf_service.iter_gateway_stream_events(
            run_id,
            session_id=session_id,
            cursor=cursor,
            limit=limit,
            last_event_id=last_event_id,
        )
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/dev-shelf/runs/{run_id}/gateway/transcript", response_model=DevShelfGatewayTranscript)
def get_dev_shelf_gateway_transcript(
    run_id: str,
    session_id: str | None = None,
) -> DevShelfGatewayTranscript:
    try:
        return dev_shelf_service.get_gateway_transcript(run_id, session_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/dev-shelf/runs/{run_id}/gateway/result", response_model=DevShelfGatewayArtifactPayload)
def get_dev_shelf_gateway_result(
    run_id: str,
    session_id: str | None = None,
) -> DevShelfGatewayArtifactPayload:
    try:
        return dev_shelf_service.get_gateway_result(run_id, session_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/dev-shelf/runs/{run_id}/gateway/candidates", response_model=DevShelfGatewayArtifactPayload)
def get_dev_shelf_gateway_candidates(
    run_id: str,
    session_id: str | None = None,
) -> DevShelfGatewayArtifactPayload:
    try:
        return dev_shelf_service.get_gateway_candidates(run_id, session_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/gateway/start", response_model=DevShelfGatewayControlResponse)
def start_dev_shelf_gateway(
    run_id: str,
    payload: DevShelfGatewayStartRequest,
) -> DevShelfGatewayControlResponse:
    try:
        return dev_shelf_service.start_gateway(run_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/gateway/abort", response_model=DevShelfGatewayControlResponse)
def abort_dev_shelf_gateway(
    run_id: str,
    payload: DevShelfGatewayAbortRequest | None = None,
) -> DevShelfGatewayControlResponse:
    try:
        return dev_shelf_service.abort_gateway(run_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/gateway/register-result", response_model=DevShelfRunDetail)
def register_dev_shelf_gateway_result(
    run_id: str,
    payload: DevShelfGatewayRegisterResultRequest | None = None,
) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.register_gateway_result(
            run_id,
            payload or DevShelfGatewayRegisterResultRequest(),
        )
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/gateway/candidates/{candidate_id}/confirm", response_model=DevShelfRunDetail)
def confirm_dev_shelf_gateway_candidate(
    run_id: str,
    candidate_id: str,
    payload: DevShelfGatewayCandidateConfirmRequest,
) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.confirm_gateway_candidate(run_id, candidate_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/gateway/candidates/{candidate_id}/revise", response_model=DevShelfRunDetail)
def revise_dev_shelf_gateway_candidate(
    run_id: str,
    candidate_id: str,
    payload: DevShelfGatewayCandidateReviseRequest,
) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.revise_gateway_candidate(run_id, candidate_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/artifacts/{artifact_id}/revise", response_model=DevShelfRunDetail)
def revise_dev_shelf_artifact(
    run_id: str,
    artifact_id: str,
    payload: DevShelfArtifactReviseRequest,
) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.revise_artifact(run_id, artifact_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/dev-shelf/runs/{run_id}/human-gates/{gate_id}/decision", response_model=DevShelfRunDetail)
def decide_dev_shelf_human_gate(
    run_id: str,
    gate_id: str,
    payload: DevShelfHumanGateDecisionRequest,
) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.decide_human_gate(run_id, gate_id, payload)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DevShelfGateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevShelfToolError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/sessions/{session_id}/messages", response_model=AcceptedResponse)
def create_message(session_id: str, payload: MessageCreate) -> AcceptedResponse:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    workflow_service.handle_user_message(session_id, payload.content)
    return AcceptedResponse()


@router.post("/api/sessions/{session_id}/confirm", response_model=AcceptedResponse)
def confirm_stage(session_id: str, payload: ConfirmRequest) -> AcceptedResponse:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        workflow_service.confirm_stage(session_id, payload.stage)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AcceptedResponse()


@router.get("/api/dev-shelf/models", response_model=DevShelfModelList)
def list_dev_shelf_models(provider: str | None = None) -> DevShelfModelList:
    return dev_shelf_service.list_available_models(provider)


@router.get("/api/dev-shelf/model-config", response_model=DevShelfModelConfig)
def get_dev_shelf_model_config() -> DevShelfModelConfig:
    return dev_shelf_service.get_model_config()


@router.post("/api/dev-shelf/model-config", response_model=DevShelfModelConfig)
def update_dev_shelf_model_config(payload: DevShelfModelConfigUpdateRequest) -> DevShelfModelConfig:
    try:
        return dev_shelf_service.update_model_config(payload)
    except DevShelfGatewayConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.websocket("/ws/sessions/{session_id}")
async def session_ws(websocket: WebSocket, session_id: str) -> None:
    if store.get_session(session_id) is None:
        await websocket.accept()
        await websocket.send_json(
            Event(
                type=EventType.ERROR,
                session_id=session_id,
                payload={"message": "Session not found"},
            ).model_dump(mode="json")
        )
        await websocket.close(code=4404)
        return

    await event_bus.connect(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.disconnect(session_id, websocket)
