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
    assert "new-run-button" in index
    assert "project-path-browse-button" in index
    assert "directory-picker-modal" in index
    assert "auto-refresh-status" in index
    assert "dev-shelf-progress-strip" in index
    assert "dev-shelf-work-status-title" in index
    assert "dev-shelf-primary-start-button" in index
    assert "dev-shelf-artifact-confirm-button" in index
    assert "实现工作台" in index
    assert "dev-shelf-execution-workbench" in index
    assert "产物与文档" in index
    assert "流程产物" in index
    assert index.index("产物与文档") < index.index("pi-agent 执行详情")
    assert "dev-shelf-gateway-panel" in index
    assert "dev-shelf-gateway-summary-line" in index
    assert "data-gateway-view=\"chat\"" in index
    assert "dev-shelf-gateway-chat" in index
    assert "dev-shelf-gateway-events" in index
    assert "执行详情" in index
    assert "dev-shelf-gateway-start-button" in index
    assert "dev-shelf-gateway-abort-button" in index
    assert "模型配置" in index
    assert "dev-shelf-gateway-provider-control" in index
    assert "dev-shelf-gateway-account-row" in index
    assert "dev-shelf-gateway-auth-row" in index
    assert "dev-shelf-gateway-auth-status" in index
    assert "dev-shelf-gateway-save-api-key-button" not in index
    assert "dev-shelf-gateway-clear-api-key-button" not in index
    assert "dev-shelf-execution-register-button" in index
    assert "dev-shelf-cancel-run-button" in index
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
    assert "/gateway/stream?" in app_js
    assert "new EventSource" in app_js
    assert "GATEWAY_STREAM_EVENT_TYPES" in app_js
    assert "handleGatewayStreamEvent" in app_js
    assert "scheduleGatewayArtifactSnapshotRefresh" in app_js
    assert "upsertLiveGatewayCandidate" in app_js
    assert "refreshGatewayArtifactSnapshots" in app_js
    assert "/gateway/transcript?" in app_js
    assert "/gateway/result?" in app_js
    assert "/gateway/candidates?" in app_js
    assert "/gateway/candidates/${encodeURIComponent(artifact.candidate_id)}/confirm" in app_js
    assert "/gateway/start" in app_js
    assert "/gateway/abort" in app_js
    assert "/gateway/register-result" in app_js
    assert "/api/dev-shelf/model-config" in app_js
    assert "/workflow/continue" in app_js
    assert "/api/dev-shelf/directories" in app_js
    assert "/cancel" in app_js
    assert "renderDevShelfGateway" in app_js
    assert "renderExecutionWorkbench" in app_js
    assert "isExecutionContext" in app_js
    assert "开始代码实现" in app_js
    assert "renderGatewayTranscript" in app_js
    assert "gatewayCompletedForCurrentPacket" in app_js
    assert "playbook.new-project-from-confirmed-requirement" in app_js
    assert "handlePrimaryDevShelfAction" in app_js
    assert "primaryActionForDetail" in app_js
    assert "canRegisterImplementationResult" in app_js
    assert "status.packet_target !== \"stage.execution\"" in app_js
    assert "status.packet_path !== detail.latest_packet?.path" in app_js
    assert "buildGatewaySummaryLine" in app_js
    assert "shouldOpenGatewayPanel" in app_js
    assert "登记实现结果" in app_js
    assert "流程产物" in app_js
    assert "代码实现" in app_js
    assert "复查" in app_js
    assert "shouldShowPrimaryStart" in app_js
    assert "loadModelConfig" in app_js
    assert "renderModelConfigControls" in app_js
    assert "currentGatewayProvider" in app_js
    assert "pi auth.json" in app_js
    assert "saveDeepSeekApiKey" not in app_js
    assert "进入执行阶段" in app_js
    assert "原始事件" in app_js
    assert "buildPendingArtifactReviewSummary" in app_js
    assert "请先查看并确认" in app_js
    assert "已写入流程状态" not in app_js
    assert "type: message_update" not in app_js


def test_frontend_collab_stream_merges_delta_and_filters_noise() -> None:
    app_js = read_static("app/static/app.js")

    assert "buildCollabStreamMessages" in app_js
    assert "appendCollabTranscriptMessages" in app_js
    assert "hasCollabAssistantTranscript" in app_js
    assert "mergeGatewayTranscript" in app_js
    assert "gatewayTranscriptMergeKey" in app_js
    assert "preferGatewayTranscriptMessage" in app_js
    assert "liveAssistantMessageKey" in app_js
    assert "normalizeCollabMessageText" in app_js
    assert "assistantDraft.text = `${assistantDraft.text}${delta}`" in app_js
    assert "flushAssistantDraft()" in app_js
    assert "cleanCollabAssistantText" in app_js
    assert "trimMultilineText(text, 6000)" in app_js
    assert "trimText(text, 1800)" in app_js
    assert "isNoisyCollabText" in app_js
    assert "packet prompt" in app_js
    assert "required_context" in app_js
    assert "runtime_events" in app_js
    assert "labelCollabToolName" in app_js
    assert "summarizeCollabToolTranscriptText" in app_js
    assert "looksLikeRawToolOutput" in app_js
    assert "工具结果已返回，原文保留在执行详情。" in app_js
    assert "payload.is_error" in app_js
    assert "return `${name} 执行完成`;" in app_js
    assert "collabScroll" in app_js
    assert "collabTypewriter" in app_js
    assert "COLLAB_TYPEWRITER_INTERVAL_MS" in app_js
    assert "displayTextForCollabMessage" in app_js
    assert "syncCollabTypewriterEntries" in app_js
    assert "advanceCollabTypewriterEntries" in app_js
    assert "collabTypewriterStepSize" in app_js
    assert "message.typewriter" in app_js
    assert "is-typing" in app_js
    assert "message.markdown && !typing" not in app_js
    assert "message.role === \"assistant\" && message.markdown" in app_js
    assert "isMarkdownTableLine" in app_js
    assert "parseMarkdownTableCells" in app_js
    assert '![\"missing\", \"rejected\"].includes(item.status)' in app_js
    assert "autoSizeCollabInput" in app_js
    assert "updateCollabChatScrollIntent" in app_js
    assert "captureScrollablePosition" in app_js
    assert "restoreCollabChatScroll" in app_js
    assert "capturePageScrollPosition" in app_js
    assert "restorePageScrollPosition" in app_js
    assert "distanceFromBottom <= SCROLL_BOTTOM_TOLERANCE_PX" in app_js
    assert "正在处理..." in app_js

    styles = read_static("app/static/styles.css")
    assert "collab-caret-blink" in styles
    assert ".collab-message.is-typing" in styles
    assert ".markdown-body table" in styles
    assert "resize: none" in styles


def test_frontend_creates_dev_shelf_runs() -> None:
    app_js = read_static("app/static/app.js")

    assert "createDevShelfRun" in app_js
    assert 'fetch("/api/dev-shelf/runs"' in app_js
    assert "project_name" in app_js
    assert "requirement" in app_js
    assert "showProjectCreatePanel" in app_js
    assert "projectIntakePanel.classList.toggle" in app_js
