from __future__ import annotations

from app.schemas.common import Event, EventType, MessageRole, StageType
from app.schemas.session import SessionState
from app.services.adapters.base import CoderAdapter
from app.services.runners import CodexRunner
from app.services.store import store


def _stage_value(stage: StageType | str) -> str:
    return stage.value if hasattr(stage, "value") else str(stage)


class CodexAdapter(CoderAdapter):
    def __init__(self, runner: CodexRunner | None = None) -> None:
        self._runner = runner or CodexRunner()

    def bootstrap_session(self, session: SessionState) -> list[Event]:
        first_message = store.list_messages(session.id)[0]
        prompt = (
            "你正在一个开发流程工作台里协助做需求梳理。"
            "请输出两部分内容："
            "第一部分是需求梳理结果，第二部分是需求确认清单。"
            "不要写代码，不要直接出 spec。"
            "输出格式固定为：\n"
            "[需求梳理]\n内容\n\n[需求确认清单]\n内容\n\n"
            f"用户输入：{first_message.content}"
        )
        reply = self._runner.run(prompt)
        drafting_content, confirmation_content = self._split_bootstrap_output(reply)

        events: list[Event] = [
            Event(
                type=EventType.USER_MESSAGE,
                session_id=session.id,
                payload={"id": first_message.id, "content": first_message.content},
            )
        ]

        drafting_message = store.add_message(session.id, MessageRole.ASSISTANT, drafting_content)
        store.update_artifact(session.id, StageType.REQUIREMENT_DRAFTING, drafting_content)
        events.append(
            Event(
                type=EventType.ASSISTANT_MESSAGE,
                session_id=session.id,
                payload={"id": drafting_message.id, "content": drafting_message.content},
            )
        )
        events.append(
            Event(
                type=EventType.ARTIFACT_UPDATED,
                session_id=session.id,
                payload={"stage": StageType.REQUIREMENT_DRAFTING.value, "content": drafting_content},
            )
        )

        store.set_stage(session.id, StageType.REQUIREMENT_CONFIRMATION)
        events.append(
            Event(
                type=EventType.STAGE_CHANGED,
                session_id=session.id,
                payload={"stage": StageType.REQUIREMENT_CONFIRMATION.value},
            )
        )

        confirmation_message = store.add_message(
            session.id,
            MessageRole.ASSISTANT,
            "需求梳理完成，已生成需求确认清单，请确认这一版是否继续。",
        )
        events.append(
            Event(
                type=EventType.ASSISTANT_MESSAGE,
                session_id=session.id,
                payload={"id": confirmation_message.id, "content": confirmation_message.content},
            )
        )

        store.update_artifact(session.id, StageType.REQUIREMENT_CONFIRMATION, confirmation_content)
        events.append(
            Event(
                type=EventType.ARTIFACT_UPDATED,
                session_id=session.id,
                payload={"stage": StageType.REQUIREMENT_CONFIRMATION.value, "content": confirmation_content},
            )
        )

        store.set_waiting(session.id, True)
        events.append(
            Event(
                type=EventType.CONFIRMATION_STATE_CHANGED,
                session_id=session.id,
                payload={"waiting_for_confirmation": True},
            )
        )
        return events

    def handle_user_message(self, session_id: str, content: str) -> list[Event]:
        user_message = store.add_message(session_id, MessageRole.USER, content)
        prompt = (
            "你正在一个开发流程工作台里协助处理当前阶段的补充意见。"
            "请根据用户新消息，输出简洁直接的回复。\n\n"
            f"用户消息：{content}"
        )
        reply = self._runner.run(prompt)
        assistant_message = store.add_message(session_id, MessageRole.ASSISTANT, reply)
        return [
            Event(
                type=EventType.USER_MESSAGE,
                session_id=session_id,
                payload={"id": user_message.id, "content": user_message.content},
            ),
            Event(
                type=EventType.ASSISTANT_MESSAGE,
                session_id=session_id,
                payload={"id": assistant_message.id, "content": assistant_message.content},
            ),
        ]

    def confirm_stage(self, session_id: str, stage: StageType | str) -> list[Event]:
        session = store.get_session(session_id)
        if session is None:
            raise ValueError("Session not found")
        if session.current_stage != stage:
            raise ValueError("Stage mismatch")
        if not session.waiting_for_confirmation:
            raise ValueError("Current stage is not waiting for confirmation")

        store.set_waiting(session_id, False)
        events: list[Event] = [
            Event(
                type=EventType.CONFIRMATION_STATE_CHANGED,
                session_id=session_id,
                payload={"waiting_for_confirmation": False},
            )
        ]

        normalized_stage = _stage_value(stage)
        if normalized_stage == StageType.REQUIREMENT_CONFIRMATION.value:
            return events + self._generate_spec(session_id)
        if normalized_stage == StageType.SPEC.value:
            return events + self._generate_execution_todo(session_id)
        if normalized_stage == StageType.EXECUTION_TODO.value:
            assistant_message = store.add_message(
                session_id,
                MessageRole.ASSISTANT,
                "执行待办已确认，当前 v1 流程结束，可以进入实际开发或后续修补。",
            )
            events.append(
                Event(
                    type=EventType.ASSISTANT_MESSAGE,
                    session_id=session_id,
                    payload={"id": assistant_message.id, "content": assistant_message.content},
                )
            )
            return events

        events.append(
            Event(
                type=EventType.ERROR,
                session_id=session_id,
                payload={"message": f"Codex adapter confirm flow for {normalized_stage} is not implemented yet."},
            )
        )
        return events

    def _generate_spec(self, session_id: str) -> list[Event]:
        current_artifact = store.get_artifact(session_id)
        prompt = (
            "你正在一个开发流程工作台里，根据已经确认的需求确认清单生成 spec。"
            "请直接输出一版简洁但结构清晰的 spec。\n\n"
            f"需求确认清单：\n{current_artifact.content}"
        )
        reply = self._runner.run(prompt)

        store.set_stage(session_id, StageType.SPEC)
        assistant_message = store.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "需求确认已通过，已生成 spec，请确认这一版是否继续。",
        )
        store.update_artifact(session_id, StageType.SPEC, reply)
        store.set_waiting(session_id, True)

        return [
            Event(
                type=EventType.STAGE_CHANGED,
                session_id=session_id,
                payload={"stage": StageType.SPEC.value},
            ),
            Event(
                type=EventType.ASSISTANT_MESSAGE,
                session_id=session_id,
                payload={"id": assistant_message.id, "content": assistant_message.content},
            ),
            Event(
                type=EventType.ARTIFACT_UPDATED,
                session_id=session_id,
                payload={"stage": StageType.SPEC.value, "content": reply},
            ),
            Event(
                type=EventType.CONFIRMATION_STATE_CHANGED,
                session_id=session_id,
                payload={"waiting_for_confirmation": True},
            ),
        ]

    def _generate_execution_todo(self, session_id: str) -> list[Event]:
        current_artifact = store.get_artifact(session_id)
        prompt = (
            "你正在一个开发流程工作台里，根据已经确认的 spec 生成 execution todo。"
            "请直接输出按模块拆分的执行待办，至少包含数据库、后端、前端、联调与收尾。\n\n"
            f"当前 spec：\n{current_artifact.content}"
        )
        reply = self._runner.run(prompt)

        store.set_stage(session_id, StageType.EXECUTION_TODO)
        assistant_message = store.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "spec 已确认，已生成执行待办，请确认这一版是否继续。",
        )
        store.update_artifact(session_id, StageType.EXECUTION_TODO, reply)
        store.set_waiting(session_id, True)

        return [
            Event(
                type=EventType.STAGE_CHANGED,
                session_id=session_id,
                payload={"stage": StageType.EXECUTION_TODO.value},
            ),
            Event(
                type=EventType.ASSISTANT_MESSAGE,
                session_id=session_id,
                payload={"id": assistant_message.id, "content": assistant_message.content},
            ),
            Event(
                type=EventType.ARTIFACT_UPDATED,
                session_id=session_id,
                payload={"stage": StageType.EXECUTION_TODO.value, "content": reply},
            ),
            Event(
                type=EventType.CONFIRMATION_STATE_CHANGED,
                session_id=session_id,
                payload={"waiting_for_confirmation": True},
            ),
        ]

    def _split_bootstrap_output(self, content: str) -> tuple[str, str]:
        draft_marker = "[需求梳理]"
        confirm_marker = "[需求确认清单]"
        if draft_marker in content and confirm_marker in content:
            draft_part = content.split(draft_marker, 1)[1].split(confirm_marker, 1)[0].strip()
            confirm_part = content.split(confirm_marker, 1)[1].strip()
            if draft_part and confirm_part:
                return draft_part, confirm_part

        fallback_draft = content.strip() or "需求梳理结果为空。"
        fallback_confirm = "- 用户是谁\n- 问题是什么\n- 目标结果是什么\n- 本期做什么/不做什么\n- 是否需要组内讨论"
        return fallback_draft, fallback_confirm
