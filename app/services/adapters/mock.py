from __future__ import annotations

from app.schemas.common import Event, EventType, MessageRole, StageType
from app.schemas.session import SessionState
from app.services.adapters.base import CoderAdapter
from app.services.store import store

ARTIFACT_TEMPLATES = {
    StageType.REQUIREMENT_DRAFTING: "需求梳理中：已记录原始需求，正在整理用户、问题、目标与范围。",
    StageType.REQUIREMENT_CONFIRMATION: "需求确认清单：\n- 用户是谁\n- 问题是什么\n- 目标结果是什么\n- 本期做什么/不做什么\n- 是否需要组内讨论",
    StageType.SPEC: "Spec v1：\n- 项目目标\n- 技术形态\n- 页面结构\n- 阶段流转\n- API 设计\n- 数据结构",
    StageType.EXECUTION_TODO: "执行待办：\n- 数据层\n- 后端接口\n- 前端页面\n- 联调验证\n- 样式收尾",
}

NEXT_STAGE = {
    StageType.REQUIREMENT_CONFIRMATION: StageType.SPEC,
    StageType.SPEC: StageType.EXECUTION_TODO,
}


def _stage_value(stage: StageType | str) -> str:
    return stage.value if hasattr(stage, "value") else str(stage)


class MockCoderAdapter(CoderAdapter):
    def bootstrap_session(self, session: SessionState) -> list[Event]:
        first_message = store.list_messages(session.id)[0]
        events = [self._make_user_event(session.id, first_message.id, first_message.content)]

        drafting_reply = (
            "我先帮你梳理需求，不直接进入 spec。"
            "当前会先整理用户、问题、目标和范围，再生成需求确认清单。"
        )
        events.append(self._make_assistant_message(session.id, drafting_reply))
        events.append(self._make_artifact_event(session.id, StageType.REQUIREMENT_DRAFTING))
        events.append(self._make_stage_event(session.id, StageType.REQUIREMENT_CONFIRMATION))
        events.append(
            self._make_assistant_message(
                session.id,
                "需求梳理完成，已生成需求确认清单，请确认这一版是否继续。",
            )
        )
        events.append(self._make_artifact_event(session.id, StageType.REQUIREMENT_CONFIRMATION))
        events.append(self._make_waiting_event(session.id, True))
        return events

    def handle_user_message(self, session_id: str, content: str) -> list[Event]:
        session = store.get_session(session_id)
        if session is None:
            return []

        user_message = store.add_message(session_id, MessageRole.USER, content)
        assistant_reply = (
            f"已收到你对 {_stage_value(session.current_stage)} 阶段的补充。"
            "我会基于当前内容更新阶段产物。"
        )
        assistant_event = self._make_assistant_message(session_id, assistant_reply)

        artifact = store.get_artifact(session_id)
        updated_content = artifact.content + f"\n\n补充意见：{content}"
        store.update_artifact(session_id, session.current_stage, updated_content)

        return [
            self._make_user_event(session_id, user_message.id, user_message.content),
            assistant_event,
            self._make_artifact_updated_event(session_id),
        ]

    def confirm_stage(self, session_id: str, stage: StageType | str) -> list[Event]:
        session = store.get_session(session_id)
        if session is None:
            raise ValueError("Session not found")
        if session.current_stage != stage:
            raise ValueError("Stage mismatch")
        if not session.waiting_for_confirmation:
            raise ValueError("Current stage is not waiting for confirmation")

        events = [self._make_waiting_event(session_id, False)]

        if stage not in NEXT_STAGE:
            events.append(self._make_assistant_message(session_id, "执行待办已确认，当前 MVP 流程结束。"))
            return events

        next_stage = NEXT_STAGE[stage]
        events.append(self._make_stage_event(session_id, next_stage))
        events.append(
            self._make_assistant_message(
                session_id,
                f"{_stage_value(stage)} 已确认，正在生成 {_stage_value(next_stage)} 阶段产物。",
            )
        )
        events.append(self._make_artifact_event(session_id, next_stage))
        events.append(
            self._make_assistant_message(
                session_id,
                f"{_stage_value(next_stage)} 已生成，请确认这一版是否继续。",
            )
        )
        events.append(self._make_waiting_event(session_id, True))
        return events

    def _make_user_event(self, session_id: str, message_id: str, content: str) -> Event:
        return Event(
            type=EventType.USER_MESSAGE,
            session_id=session_id,
            payload={"id": message_id, "content": content},
        )

    def _make_assistant_message(self, session_id: str, content: str) -> Event:
        message = store.add_message(session_id, MessageRole.ASSISTANT, content)
        return Event(
            type=EventType.ASSISTANT_MESSAGE,
            session_id=session_id,
            payload={"id": message.id, "content": message.content},
        )

    def _make_stage_event(self, session_id: str, stage: StageType) -> Event:
        store.set_stage(session_id, stage)
        return Event(
            type=EventType.STAGE_CHANGED,
            session_id=session_id,
            payload={"stage": stage.value},
        )

    def _make_waiting_event(self, session_id: str, waiting: bool) -> Event:
        store.set_waiting(session_id, waiting)
        return Event(
            type=EventType.CONFIRMATION_STATE_CHANGED,
            session_id=session_id,
            payload={"waiting_for_confirmation": waiting},
        )

    def _make_artifact_event(self, session_id: str, stage: StageType) -> Event:
        store.update_artifact(session_id, stage, ARTIFACT_TEMPLATES[stage])
        return self._make_artifact_updated_event(session_id)

    def _make_artifact_updated_event(self, session_id: str) -> Event:
        artifact = store.get_artifact(session_id)
        return Event(
            type=EventType.ARTIFACT_UPDATED,
            session_id=session_id,
            payload={"stage": _stage_value(artifact.stage), "content": artifact.content},
        )
