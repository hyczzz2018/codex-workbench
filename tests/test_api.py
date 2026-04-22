from app.api.routes import confirm_stage, create_session, get_artifact, get_session, list_messages
from app.main import index
from app.schemas.session import ConfirmRequest, MessageList, SessionCreate, SessionRead


def test_index_serves_html_file() -> None:
    response = index()
    assert str(response.path).endswith("app/static/index.html")


def test_index_only_exposes_dev_shelf_workbench_ui() -> None:
    response = index()
    html = response.path.read_text(encoding="utf-8")

    for legacy_text in [
        "旧会话入口",
        "当前会话",
        "阶段面板",
        "旧会话产物",
        "历史记录",
        "活动流",
        "Legacy",
        "Activity",
        "兼容旧流程",
        "旧会话消息",
    ]:
        assert legacy_text not in html

    for dev_shelf_text in [
        "任务列表",
        "当前任务进度",
        "等待确认",
        "中间产物",
        "产物预览",
        "最新推进建议",
    ]:
        assert dev_shelf_text in html


def test_session_flow() -> None:
    session = create_session(
        SessionCreate(
            title="Demo",
            raw_input="我要做一个网页，请根据 dev-shelf 先帮我梳理需求。",
        )
    )
    assert isinstance(session, SessionRead)
    session_id = session.id
    assert session.current_stage == "requirement_confirmation"
    assert session.waiting_for_confirmation is True

    messages = list_messages(session_id)
    assert isinstance(messages, MessageList)
    assert len(messages.items) >= 3

    artifact = get_artifact(session_id)
    assert artifact["stage"] == "requirement_confirmation"

    confirm = confirm_stage(
        session_id,
        ConfirmRequest(stage="requirement_confirmation"),
    )
    assert confirm.accepted is True

    session_after = get_session(session_id)
    assert session_after.current_stage == "spec"
    assert session_after.waiting_for_confirmation is True
