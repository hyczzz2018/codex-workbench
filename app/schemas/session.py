from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import APIModel, MessageRole, StageType


class SessionCreate(APIModel):
    title: str
    raw_input: str


class SessionRead(APIModel):
    id: str
    title: str
    raw_input: str
    current_stage: StageType
    waiting_for_confirmation: bool
    created_at: datetime
    updated_at: datetime


class MessageCreate(APIModel):
    content: str


class MessageRead(APIModel):
    id: str
    session_id: str
    role: MessageRole
    content: str
    created_at: datetime


class MessageList(APIModel):
    items: list[MessageRead]


class ArtifactRead(APIModel):
    stage: StageType
    content: str
    updated_at: datetime


class ConfirmRequest(APIModel):
    stage: StageType


class AcceptedResponse(APIModel):
    accepted: bool = True


class SessionState(APIModel):
    id: str
    title: str
    raw_input: str
    current_stage: StageType = StageType.REQUIREMENT_DRAFTING
    waiting_for_confirmation: bool = False
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ArtifactState(APIModel):
    session_id: str
    stage: StageType
    content: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)
