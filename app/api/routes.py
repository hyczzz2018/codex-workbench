from __future__ import annotations

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.schemas.common import Event, EventType
from app.schemas.dev_shelf import DevShelfHumanGateDecisionRequest, DevShelfRunDetail, DevShelfRunList
from app.schemas.session import AcceptedResponse, ConfirmRequest, MessageCreate, MessageList, SessionCreate, SessionRead
from app.services.dev_shelf import DevShelfGateConflict, DevShelfRunNotFound, DevShelfToolError, dev_shelf_service
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


@router.get("/api/dev-shelf/runs/{run_id}", response_model=DevShelfRunDetail)
def get_dev_shelf_run(run_id: str) -> DevShelfRunDetail:
    try:
        return dev_shelf_service.get_run(run_id)
    except DevShelfRunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
