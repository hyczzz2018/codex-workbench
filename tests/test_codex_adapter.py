from app.schemas.common import StageType
from app.schemas.session import SessionCreate
from app.services.adapters.codex import CodexAdapter
from app.services.store import store


class FakeRunner:
    def run(self, prompt: str) -> str:
        if "需求确认清单" in prompt and "生成 spec" in prompt:
            return "## Spec\n- 项目目标\n- 技术形态\n- 页面结构"
        if "根据已经确认的 spec 生成 execution todo" in prompt:
            return "## Execution Todo\n- 数据库\n- 后端\n- 前端\n- 联调\n- 收尾"
        return "[需求梳理]\n用户是开发者，目标是把流程可视化。\n\n[需求确认清单]\n- 用户是谁\n- 问题是什么\n- 目标结果是什么"


def test_codex_adapter_bootstrap_updates_stage_and_waiting() -> None:
    session = store.create_session(
        SessionCreate(
            title="Demo",
            raw_input="我要做一个网页，请根据 dev-shelf 先帮我梳理需求。",
        )
    )
    adapter = CodexAdapter(runner=FakeRunner())

    events = adapter.bootstrap_session(session)

    assert any(event.type == "stage_changed" for event in events)
    assert any(event.type == "confirmation_state_changed" for event in events)
    updated_session = store.get_session(session.id)
    assert updated_session is not None
    assert updated_session.current_stage == StageType.REQUIREMENT_CONFIRMATION
    assert updated_session.waiting_for_confirmation is True
    artifact = store.get_artifact(session.id)
    assert artifact.stage == StageType.REQUIREMENT_CONFIRMATION
    assert "用户是谁" in artifact.content


def test_codex_adapter_confirm_requirement_confirmation_to_spec() -> None:
    session = store.create_session(
        SessionCreate(
            title="Spec Demo",
            raw_input="我要做一个网页，请根据 dev-shelf 先帮我梳理需求。",
        )
    )
    adapter = CodexAdapter(runner=FakeRunner())
    adapter.bootstrap_session(session)

    events = adapter.confirm_stage(session.id, StageType.REQUIREMENT_CONFIRMATION)

    assert any(event.type == "stage_changed" and event.payload["stage"] == "spec" for event in events)
    updated_session = store.get_session(session.id)
    assert updated_session is not None
    assert updated_session.current_stage == StageType.SPEC
    assert updated_session.waiting_for_confirmation is True
    artifact = store.get_artifact(session.id)
    assert artifact.stage == StageType.SPEC
    assert "## Spec" in artifact.content


def test_codex_adapter_confirm_spec_to_execution_todo() -> None:
    session = store.create_session(
        SessionCreate(
            title="Todo Demo",
            raw_input="我要做一个网页，请根据 dev-shelf 先帮我梳理需求。",
        )
    )
    adapter = CodexAdapter(runner=FakeRunner())
    adapter.bootstrap_session(session)
    adapter.confirm_stage(session.id, StageType.REQUIREMENT_CONFIRMATION)

    events = adapter.confirm_stage(session.id, StageType.SPEC)

    assert any(
        event.type == "stage_changed" and event.payload["stage"] == "execution_todo"
        for event in events
    )
    updated_session = store.get_session(session.id)
    assert updated_session is not None
    assert updated_session.current_stage == StageType.EXECUTION_TODO
    assert updated_session.waiting_for_confirmation is True
    artifact = store.get_artifact(session.id)
    assert artifact.stage == StageType.EXECUTION_TODO
    assert "## Execution Todo" in artifact.content


def test_codex_adapter_confirm_execution_todo_finishes_v1_flow() -> None:
    session = store.create_session(
        SessionCreate(
            title="Finish Demo",
            raw_input="我要做一个网页，请根据 dev-shelf 先帮我梳理需求。",
        )
    )
    adapter = CodexAdapter(runner=FakeRunner())
    adapter.bootstrap_session(session)
    adapter.confirm_stage(session.id, StageType.REQUIREMENT_CONFIRMATION)
    adapter.confirm_stage(session.id, StageType.SPEC)

    events = adapter.confirm_stage(session.id, StageType.EXECUTION_TODO)

    assert any(
        event.type == "assistant_message" and "当前 v1 流程结束" in event.payload["content"]
        for event in events
    )
    updated_session = store.get_session(session.id)
    assert updated_session is not None
    assert updated_session.current_stage == StageType.EXECUTION_TODO
    assert updated_session.waiting_for_confirmation is False
