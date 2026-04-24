from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_static(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workbench_page_exposes_project_intake_and_execution_detail() -> None:
    index = read_static("app/static/index.html")

    assert "工作台" in index
    assert "创建项目" in index
    assert "project-create-form" in index
    assert "project-requirement-input" in index
    assert "auto-refresh-status" in index
    assert "dev-shelf-gateway-events" in index
    assert "执行详情" in index
    assert "dev-shelf-gateway-start-button" in index
    assert "dev-shelf-gateway-abort-button" in index
    assert "/static/app.js?v=" in index


def test_frontend_does_not_expose_web_confirmation_actions() -> None:
    static_text = "\n".join(
        [
            read_static("app/static/index.html"),
            read_static("app/static/app.js"),
        ]
    )

    forbidden_text = [
        "确认通过",
        "退回修改",
        "写入中",
        "确认备注",
        "handleDevShelfGateDecision",
        "/human-gates/",
    ]
    for text in forbidden_text:
        assert text not in static_text


def test_frontend_has_polling_auto_refresh() -> None:
    app_js = read_static("app/static/app.js")

    assert "AUTO_REFRESH_INTERVAL_MS = 5000" in app_js
    assert "setInterval" in app_js
    assert "refreshDevShelfSnapshot({ silent: true })" in app_js


def test_frontend_reads_gateway_status_and_events() -> None:
    app_js = read_static("app/static/app.js")

    assert "/gateway/latest" in app_js
    assert "/gateway/events?" in app_js
    assert "/gateway/result?" in app_js
    assert "/gateway/candidates?" in app_js
    assert "/gateway/start" in app_js
    assert "/gateway/abort" in app_js
    assert "renderDevShelfGateway" in app_js


def test_frontend_creates_dev_shelf_runs() -> None:
    app_js = read_static("app/static/app.js")

    assert "createDevShelfRun" in app_js
    assert 'fetch("/api/dev-shelf/runs"' in app_js
    assert "project_name" in app_js
    assert "requirement" in app_js
