from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.common import Event, StageType
from app.schemas.session import SessionState


class CoderAdapter(ABC):
    @abstractmethod
    def bootstrap_session(self, session: SessionState) -> list[Event]:
        raise NotImplementedError

    @abstractmethod
    def handle_user_message(self, session_id: str, content: str) -> list[Event]:
        raise NotImplementedError

    @abstractmethod
    def confirm_stage(self, session_id: str, stage: StageType) -> list[Event]:
        raise NotImplementedError
