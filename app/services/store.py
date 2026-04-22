from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from uuid import uuid4

from app.schemas.common import MessageRole, StageType
from app.schemas.session import ArtifactState, MessageRead, SessionCreate, SessionState


class InMemoryStore:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self.messages: dict[str, list[MessageRead]] = defaultdict(list)
        self.artifacts: dict[str, ArtifactState] = {}

    def create_session(self, payload: SessionCreate) -> SessionState:
        session_id = f"sess_{uuid4().hex[:8]}"
        session = SessionState(
            id=session_id,
            title=payload.title,
            raw_input=payload.raw_input,
        )
        self.sessions[session_id] = session
        self.artifacts[session_id] = ArtifactState(
            session_id=session_id,
            stage=StageType.REQUIREMENT_DRAFTING,
            content="",
        )
        self.add_message(session_id, MessageRole.USER, payload.raw_input)
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        return self.sessions.get(session_id)

    def list_messages(self, session_id: str) -> list[MessageRead]:
        return self.messages[session_id]

    def add_message(self, session_id: str, role: MessageRole, content: str) -> MessageRead:
        message = MessageRead(
            id=f"msg_{uuid4().hex[:8]}",
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(),
        )
        self.messages[session_id].append(message)
        self.touch_session(session_id)
        return message

    def get_artifact(self, session_id: str) -> ArtifactState:
        return self.artifacts[session_id]

    def update_artifact(self, session_id: str, stage: StageType, content: str) -> ArtifactState:
        artifact = ArtifactState(
            session_id=session_id,
            stage=stage,
            content=content,
            updated_at=datetime.now(),
        )
        self.artifacts[session_id] = artifact
        self.touch_session(session_id)
        return artifact

    def set_stage(self, session_id: str, stage: StageType) -> SessionState:
        session = self.sessions[session_id]
        session.current_stage = stage
        session.updated_at = datetime.now()
        return session

    def set_waiting(self, session_id: str, waiting: bool) -> SessionState:
        session = self.sessions[session_id]
        session.waiting_for_confirmation = waiting
        session.updated_at = datetime.now()
        return session

    def touch_session(self, session_id: str) -> None:
        self.sessions[session_id].updated_at = datetime.now()


store = InMemoryStore()
