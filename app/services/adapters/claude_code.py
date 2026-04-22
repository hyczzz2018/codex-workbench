from __future__ import annotations

from app.schemas.common import Event, EventType, StageType
from app.schemas.session import SessionState
from app.services.adapters.base import CoderAdapter


class ClaudeCodeAdapter(CoderAdapter):
    def bootstrap_session(self, session: SessionState) -> list[Event]:
        return [
            Event(
                type=EventType.ERROR,
                session_id=session.id,
                payload={"message": "Claude Code adapter is not connected yet."},
            )
        ]

    def handle_user_message(self, session_id: str, content: str) -> list[Event]:
        return [
            Event(
                type=EventType.ERROR,
                session_id=session_id,
                payload={"message": "Claude Code adapter is not connected yet."},
            )
        ]

    def confirm_stage(self, session_id: str, stage: StageType) -> list[Event]:
        return [
            Event(
                type=EventType.ERROR,
                session_id=session_id,
                payload={"message": "Claude Code adapter is not connected yet."},
            )
        ]
