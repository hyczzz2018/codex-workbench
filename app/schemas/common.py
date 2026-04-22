from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class StageType(str, Enum):
    REQUIREMENT_DRAFTING = "requirement_drafting"
    REQUIREMENT_CONFIRMATION = "requirement_confirmation"
    SPEC = "spec"
    EXECUTION_TODO = "execution_todo"


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    STAGE_CHANGED = "stage_changed"
    ARTIFACT_UPDATED = "artifact_updated"
    CONFIRMATION_STATE_CHANGED = "confirmation_state_changed"
    ERROR = "error"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class APIModel(BaseModel):
    model_config = {"use_enum_values": True}


class Event(APIModel):
    type: EventType
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    payload: dict
