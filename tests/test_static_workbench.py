from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_static(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workbench_page_exposes_read_only_positioning() -> None:
    index = read_static("app/static/index.html")

    assert "任务观察台" in index
    assert "确认、继续执行和 AI 交流请在终端完成" in index
    assert "auto-refresh-status" in index
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
