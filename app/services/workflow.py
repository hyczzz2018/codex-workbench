from __future__ import annotations

from app.schemas.common import Event, StageType
from app.schemas.session import SessionState
from app.services.adapters import CoderAdapter, get_coder_adapter
from app.services.events import event_bus


class WorkflowService:
    def __init__(self, adapter: CoderAdapter) -> None:
        self._adapter = adapter

    def bootstrap_session(self, session: SessionState) -> None:
        self._publish(self._adapter.bootstrap_session(session))

    def handle_user_message(self, session_id: str, content: str) -> None:
        self._publish(self._adapter.handle_user_message(session_id, content))

    def confirm_stage(self, session_id: str, stage: StageType) -> None:
        self._publish(self._adapter.confirm_stage(session_id, stage))

    def _publish(self, events: list[Event]) -> None:
        for event in events:
            event_bus.publish_sync(event)


workflow_service = WorkflowService(adapter=get_coder_adapter())
