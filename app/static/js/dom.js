// DOM references, labels, constants, render functions
import { state } from './state.js';
const AUTO_REFRESH_INTERVAL_MS = 5000;
const SCROLL_BOTTOM_TOLERANCE_PX = 96;
const COLLAB_TYPEWRITER_INTERVAL_MS = 24;
const COLLAB_TYPEWRITER_MIN_CHARS_PER_TICK = 1;
const COLLAB_TYPEWRITER_MAX_CHARS_PER_TICK = 4;
const GATEWAY_ARTIFACT_REFRESH_DELAY_MS = 400;
const GATEWAY_ARTIFACT_REFRESH_RETRY_MS = 1500;
const GATEWAY_ARTIFACT_REFRESH_MAX_ATTEMPTS = 4;
const GATEWAY_STREAM_EVENT_TYPES = [
  "assistant_delta",
  "assistant_message",
  "tool_call",
  "tool_result",
  "file_write",
  "status",
  "artifact_candidate",
  "running_service",
  "error",
];
let autoRefreshTimer = null;
let gatewayEventSource = null;
let gatewayStreamRunId = null;
let gatewayStreamSessionId = null;
let gatewayRenderTimer = null;
let gatewayArtifactRefreshTimer = null;
let gatewayArtifactRefreshAttempts = 0;
let gatewayArtifactRefreshInFlight = false;
let collabTypewriterTimer = null;


const devShelfStageLabels = {
  requirements_drafting: "需求梳理",
  requirement_confirmation: "需求确认",
  confirmed_requirement: "需求已确认",
  skill_selection: "流程选择",
  spec_drafting: "Spec 起草",
  spec_confirmation: "Spec 确认",
  reuse_decision: "复用判断",
  implementation_planning: "执行待办规划",
  execution: "代码实现",
  review: "复查",
  completed: "已完成",
};

const runStatusLabels = {
  in_progress: "进行中",
  awaiting_human: "等待人工确认",
  blocked: "已阻塞",
  ready_for_next_stage: "可继续推进",
  completed: "已完成",
  rolled_back: "已回退",
  cancelled: "已取消",
};

const decisionLabels = {
  wait_for_human: "等待人工确认",
  run_manifest: "准备生成下一份产物",
  enter_stage: "准备进入下一阶段",
  no_route: "暂无可推进步骤",
  blocked: "已阻塞",
};

const artifactStatusLabels = {
  missing: "未产出",
  draft: "草稿",
  in_review: "待确认",
  approved: "已确认",
  rejected: "已退回",
  done: "已完成",
};

const artifactLabels = {
  requirement_draft: "需求草稿",
  requirement_confirmation_checklist: "需求确认清单",
  existing_project_analysis: "现有项目分析",
  implementation_plan: "推进计划",
  spec: "项目 spec",
  reuse_decision: "复用判断",
  execution_todo: "执行待办",
  implementation_result: "实现结果",
  review_report: "复查报告",
  final_summary: "最终总结",
};

const targetLabels = {
  "template.requirement-confirmation-checklist": "生成需求确认清单",
  "template.existing-project-analysis": "生成现有项目分析",
  "template.spec": "生成 spec",
  "template.reuse-decision": "生成复用判断",
  "template.execution-todo": "生成执行待办",
  "playbook.new-project-from-confirmed-requirement": "生成书架推进计划",
  "stage.confirmed_requirement": "进入已确认需求",
  "stage.spec_drafting": "进入 spec 起草",
  "stage.reuse_decision": "进入复用判断",
  "stage.implementation_planning": "进入执行待办规划",
  "stage.execution": "进入代码实现",
  "stage.review": "进入复查",
};


const elements = {
  projectIntakePanel: document.querySelector("#project-intake-panel"),
  projectCreateForm: document.querySelector("#project-create-form"),
  projectCreateBadge: document.querySelector("#project-create-badge"),
  projectNameInput: document.querySelector("#project-name-input"),
  projectTaskTypeInput: document.querySelector("#project-task-type-input"),
  projectContextInput: document.querySelector("#project-context-input"),
  projectPathInput: document.querySelector("#project-path-input"),
  projectPathBrowseButton: document.querySelector("#project-path-browse-button"),
  projectRequirementInput: document.querySelector("#project-requirement-input"),
  projectWorkspaceConfirmedInput: document.querySelector("#project-workspace-confirmed-input"),
  projectCreateButton: document.querySelector("#project-create-button"),
  projectCreateStatus: document.querySelector("#project-create-status"),
  newRunButton: document.querySelector("#new-run-button"),
  refreshRunsButton: document.querySelector("#refresh-runs-button"),
  autoRefreshStatus: document.querySelector("#auto-refresh-status"),
  backendHealthStatus: document.querySelector("#backend-health-status"),
  devShelfRunList: document.querySelector("#dev-shelf-run-list"),
  devShelfRunBadge: document.querySelector("#dev-shelf-run-badge"),
  devShelfCancelRunButton: document.querySelector("#dev-shelf-cancel-run-button"),
  devShelfRunActionStatus: document.querySelector("#dev-shelf-run-action-status"),
  devShelfRunId: document.querySelector("#dev-shelf-run-id"),
  devShelfRunStage: document.querySelector("#dev-shelf-run-stage"),
  devShelfRunStatus: document.querySelector("#dev-shelf-run-status"),
  devShelfPacketTarget: document.querySelector("#dev-shelf-packet-target"),
  devShelfProgressStrip: document.querySelector("#dev-shelf-progress-strip"),
  devShelfWorkStatusTitle: document.querySelector("#dev-shelf-work-status-title"),
  devShelfWorkStatusMessage: document.querySelector("#dev-shelf-work-status-message"),
  devShelfPrimaryActions: document.querySelector("#dev-shelf-primary-actions"),
  devShelfPrimaryStartButton: document.querySelector("#dev-shelf-primary-start-button"),
  devShelfExecutionWorkbench: document.querySelector("#dev-shelf-execution-workbench"),
  devShelfExecutionMeta: document.querySelector("#dev-shelf-execution-meta"),
  devShelfExecutionBadge: document.querySelector("#dev-shelf-execution-badge"),
  devShelfExecutionWorkspace: document.querySelector("#dev-shelf-execution-workspace"),
  devShelfExecutionPacket: document.querySelector("#dev-shelf-execution-packet"),
  devShelfExecutionResultStatus: document.querySelector("#dev-shelf-execution-result-status"),
  devShelfExecutionLastOutput: document.querySelector("#dev-shelf-execution-last-output"),
  devShelfExecutionRegisterButton: document.querySelector("#dev-shelf-execution-register-button"),
  devShelfExecutionResult: document.querySelector("#dev-shelf-execution-result"),
  devShelfGatewayPanel: document.querySelector("#dev-shelf-gateway-panel"),
  devShelfGatewaySummaryLine: document.querySelector("#dev-shelf-gateway-summary-line"),
  devShelfGatewayStatus: document.querySelector("#dev-shelf-gateway-status"),
  devShelfGatewaySession: document.querySelector("#dev-shelf-gateway-session"),
  devShelfGatewayModel: document.querySelector("#dev-shelf-gateway-model"),
  devShelfGatewayEventCount: document.querySelector("#dev-shelf-gateway-event-count"),
  devShelfGatewayCandidateCount: document.querySelector("#dev-shelf-gateway-candidate-count"),
  devShelfGatewayViewButtons: document.querySelectorAll("[data-gateway-view]"),
  devShelfGatewayChatPanel: document.querySelector("#dev-shelf-gateway-chat-panel"),
  devShelfGatewayEventsPanel: document.querySelector("#dev-shelf-gateway-events-panel"),
  devShelfGatewaySummaryPanel: document.querySelector("#dev-shelf-gateway-summary-panel"),
  devShelfGatewayChat: document.querySelector("#dev-shelf-gateway-chat"),
  devShelfGatewayEvents: document.querySelector("#dev-shelf-gateway-events"),
  devShelfGatewaySummary: document.querySelector("#dev-shelf-gateway-summary"),
  devShelfGatewayAccount: document.querySelector("#dev-shelf-gateway-account"),
  devShelfGatewayAccountRow: document.querySelector("#dev-shelf-gateway-account-row"),
  devShelfGatewayProviderControl: document.querySelector("#dev-shelf-gateway-provider-control"),
  devShelfGatewayModelControl: document.querySelector("#dev-shelf-gateway-model-control"),
  devShelfGatewayRefreshModelsButton: document.querySelector("#dev-shelf-gateway-refresh-models-button"),
  devShelfGatewayModelConfigStatus: document.querySelector("#dev-shelf-gateway-model-config-status"),
  devShelfGatewayAuthRow: document.querySelector("#dev-shelf-gateway-auth-row"),
  devShelfGatewayAuthStatus: document.querySelector("#dev-shelf-gateway-auth-status"),
  devShelfGatewayAuthSource: document.querySelector("#dev-shelf-gateway-auth-source"),
  devShelfGatewayLightMode: document.querySelector("#dev-shelf-gateway-light-mode"),
  devShelfGatewayStartButton: document.querySelector("#dev-shelf-gateway-start-button"),
  devShelfGatewayAbortButton: document.querySelector("#dev-shelf-gateway-abort-button"),
  devShelfGatewayControlStatus: document.querySelector("#dev-shelf-gateway-control-status"),
  devShelfFlowDocuments: document.querySelector("#dev-shelf-flow-documents"),
  devShelfFlowDocumentsCount: document.querySelector("#dev-shelf-flow-documents-count"),
  devShelfArtifacts: document.querySelector("#dev-shelf-artifacts"),
  devShelfCollabStatus: document.querySelector("#dev-shelf-collab-status"),
  devShelfCollabChat: document.querySelector("#dev-shelf-collab-chat"),
  devShelfCollabNewMessageButton: document.querySelector("#dev-shelf-collab-new-message-button"),
  devShelfCollabForm: document.querySelector("#dev-shelf-collab-form"),
  devShelfCollabInput: document.querySelector("#dev-shelf-collab-input"),
  devShelfCollabSubmitButton: document.querySelector("#dev-shelf-collab-submit-button"),
  devShelfCollabAbortButton: document.querySelector("#dev-shelf-collab-abort-button"),
  devShelfCollabStatusText: document.querySelector("#dev-shelf-collab-status-text"),
  artifactPreviewModal: document.querySelector("#artifact-preview-modal"),
  artifactPreviewCloseButton: document.querySelector("#artifact-preview-close-button"),
  artifactPreviewTitle: document.querySelector("#artifact-preview-title"),
  devShelfArtifactPreviewMeta: document.querySelector("#dev-shelf-artifact-preview-meta"),
  devShelfArtifactActions: document.querySelector("#dev-shelf-artifact-actions"),
  devShelfArtifactConfirmButton: document.querySelector("#dev-shelf-artifact-confirm-button"),
  devShelfArtifactReviseToggleButton: document.querySelector("#dev-shelf-artifact-revise-toggle-button"),
  devShelfArtifactFeedbackRow: document.querySelector("#dev-shelf-artifact-feedback-row"),
  devShelfArtifactFeedbackInput: document.querySelector("#dev-shelf-artifact-feedback-input"),
  devShelfArtifactReviseSubmitButton: document.querySelector("#dev-shelf-artifact-revise-submit-button"),
  devShelfArtifactActionStatus: document.querySelector("#dev-shelf-artifact-action-status"),
  devShelfArtifactPreview: document.querySelector("#dev-shelf-artifact-preview"),
  devShelfPacketMeta: document.querySelector("#dev-shelf-packet-meta"),
  directoryPickerModal: document.querySelector("#directory-picker-modal"),
  directoryPickerCloseButton: document.querySelector("#directory-picker-close-button"),
  directoryPickerUpButton: document.querySelector("#directory-picker-up-button"),
  directoryPickerCurrent: document.querySelector("#directory-picker-current"),
  directoryPickerList: document.querySelector("#directory-picker-list"),
  directoryNewNameInput: document.querySelector("#directory-new-name-input"),
  directoryNewButton: document.querySelector("#directory-new-button"),
  directoryPickerStatus: document.querySelector("#directory-picker-status"),
  directoryChooseButton: document.querySelector("#directory-choose-button"),
};

elements.projectCreateForm.addEventListener("submit", (event) => createDevShelfRun(event));
elements.newRunButton.addEventListener("click", () => showProjectCreatePanel());
elements.projectPathBrowseButton.addEventListener("click", () => openProjectDirectoryPicker());
elements.refreshRunsButton.addEventListener("click", () => loadDevShelfRuns());
elements.devShelfCancelRunButton.addEventListener("click", () => cancelDevShelfRun());
elements.devShelfArtifactConfirmButton.addEventListener("click", () => confirmSelectedGatewayArtifact());
elements.devShelfArtifactReviseToggleButton.addEventListener("click", () => toggleArtifactFeedbackInput());
elements.devShelfArtifactReviseSubmitButton.addEventListener("click", () => submitArtifactRevisionFromReview());
elements.devShelfPrimaryStartButton.addEventListener("click", () => handlePrimaryDevShelfAction());
elements.devShelfExecutionRegisterButton.addEventListener("click", () => registerDevShelfGatewayResult());
elements.devShelfGatewayStartButton.addEventListener("click", () => startDevShelfGateway());
elements.devShelfGatewayAbortButton.addEventListener("click", () => abortDevShelfGateway());
elements.devShelfCollabForm.addEventListener("submit", (event) => submitCollabFeedback(event));
elements.devShelfCollabAbortButton.addEventListener("click", () => abortDevShelfGateway());
elements.devShelfCollabChat.addEventListener("scroll", () => updateCollabChatScrollIntent());
elements.devShelfCollabNewMessageButton.addEventListener("click", () => scrollCollabChatToLatest());
elements.devShelfCollabInput.addEventListener("input", () => autoSizeCollabInput());
elements.artifactPreviewCloseButton.addEventListener("click", () => closeArtifactPreviewModal());
elements.artifactPreviewModal.addEventListener("click", (event) => {
  if (event.target === elements.artifactPreviewModal) {
    closeArtifactPreviewModal();
  }
});
elements.devShelfGatewayViewButtons.forEach((button) => {
  button.addEventListener("click", () => setGatewayView(button.dataset.gatewayView));
});
elements.devShelfGatewayPanel.addEventListener("toggle", () => {
  if (!state.devShelf.gateway.renderingPanel) {
    state.devShelf.gateway.panelOpen = elements.devShelfGatewayPanel.open;
  }
});
elements.directoryPickerCloseButton.addEventListener("click", () => closeProjectDirectoryPicker());
elements.directoryPickerUpButton.addEventListener("click", () => {
  if (state.devShelf.dirPicker.parentPath) {
    loadProjectDirectories(state.devShelf.dirPicker.parentPath);
  }
});
elements.directoryNewButton.addEventListener("click", () => createProjectDirectory());
elements.directoryChooseButton.addEventListener("click", () => chooseCurrentProjectDirectory());

renderDevShelf();
loadBackendHealth();
loadDevShelfRuns();
loadModelConfig();
loadAvailableModels();
startDevShelfAutoRefresh();

elements.devShelfGatewayRefreshModelsButton.addEventListener("click", () => loadAvailableModels());
elements.devShelfGatewayProviderControl.addEventListener("change", () => handleGatewayProviderChange());
elements.devShelfGatewayModelControl.addEventListener("change", () => saveModelConfigSelection());
elements.devShelfGatewayAccount.addEventListener("change", () => saveModelConfigSelection());


function renderDevShelfPacket(packet) {
  const previews = pendingCandidatePreviewArtifacts();
  if (previews.length) {
    elements.devShelfPacketMeta.textContent = `等待确认 · ${formatArtifactNames(previews)}`;
    return;
  }
  if (!packet) {
    elements.devShelfPacketMeta.textContent = "暂无推进建议。";
    return;
  }

  elements.devShelfPacketMeta.textContent =
    `第 ${packet.sequence || "-"} 轮 · ${labelDecision(packet.decision_type)} · ${labelPacketReady(packet.ready)}`;
}

function formatTarget(target) {
  if (Array.isArray(target)) {
    return target.length ? target.map(labelTarget).join("、") : "-";
  }
  return labelTarget(target);
}

function labelStage(value) {
  if (!value) {
    return "-";
  }
  return devShelfStageLabels[value] || "未知阶段";
}

function labelRunStatus(value) {
  if (!value) {
    return "-";
  }
  return runStatusLabels[value] || "未知状态";
}

function labelDecision(value) {
  if (!value) {
    return "-";
  }
  return decisionLabels[value] || "需要查看详情";
}

function labelArtifactStatus(value) {
  if (!value) {
    return "-";
  }
  return artifactStatusLabels[value] || "未知状态";
}

function labelGateStatus(value) {
  if (value === "pending") {
    return "待确认";
  }
  if (value === "approved") {
    return "已确认";
  }
  if (value === "rejected") {
    return "已退回";
  }
  if (value === "waived") {
    return "已跳过";
  }
  return value ? "未知状态" : "-";
}

function labelGatewayStatus(value) {
  if (value === "completed") {
    return "已完成";
  }
  if (value === "failed") {
    return "失败";
  }
  if (value === "starting") {
    return "启动中";
  }
  return value || "-";
}

function gatewayStatusClass(value) {
  if (value === "completed") {
    return "ready";
  }
  if (value === "failed") {
    return "waiting";
  }
  return "subtle";
}

function labelGatewayControlStatus(value) {
  const labels = {
    started: "已启动",
    aborted: "已中止",
    abort_requested: "已请求中止",
    not_running: "没有运行中的 Gateway",
  };
  return labels[value] || value || "-";
}

function labelGatewayTranscriptMessage(message) {
  if (message.role === "assistant") {
    return "pi-agent";
  }
  if (message.role === "tool") {
    return message.kind === "tool" ? "工具" : "工具事件";
  }
  if (message.role === "error") {
    return "错误";
  }
  return "系统";
}

function labelRuntimeEventKind(value) {
  const labels = {
    response: "响应",
    text: "文本",
    tool: "工具",
    stderr: "错误输出",
    lifecycle: "生命周期",
  };
  return labels[value] || "事件";
}

function labelWorkbenchStreamEvent(value) {
  const labels = {
    assistant_delta: "回复片段",
    assistant_message: "完整回复",
    tool_call: "工具调用",
    tool_result: "工具结果",
    file_write: "文件写入",
    status: "状态",
    artifact_candidate: "产物候选",
    running_service: "运行服务",
    error: "错误",
  };
  return labels[value] || "事件";
}

function labelRuntimeCommand(value) {
  const labels = {
    get_state: "读取流程状态",
    read_file: "读取文件",
    write_file: "写入文件",
    list_files: "列出文件",
    apply_patch: "应用补丁",
    shell: "运行命令",
  };
  return labels[value] ? `${labels[value]}：${value}` : `运行命令：${value}`;
}

function labelArtifact(value) {
  if (!value) {
    return "相关产物";
  }
  return artifactLabels[value] || "相关产物";
}

function labelTarget(value) {
  if (!value) {
    return "-";
  }
  if (Array.isArray(value)) {
    return formatTarget(value);
  }
  return targetLabels[value] || artifactLabels[value] || "查看下一步";
}

function labelPacketReady(value) {
  if (value === true) {
    return "可继续";
  }
  if (value === false) {
    return "暂不可推进";
  }
  return "状态未知";
}

function runStatusClass(value) {
  if (value === "completed" || value === "ready_for_next_stage") {
    return "ready";
  }
  if (value === "blocked" || value === "awaiting_human") {
    return "waiting";
  }
  return "subtle";
}

function summarizeRuntimeEvent(event) {
  const raw = event.raw || {};
  if (event.kind === "text" && raw.delta) {
    return raw.delta;
  }
  if (event.kind === "stderr" && raw.line) {
    return raw.line;
  }
  if (raw.type === "message_update") {
    const subType = raw.assistantMessageEvent?.type;
    if (subType?.startsWith("toolcall_")) {
      const tool = Array.isArray(raw.message?.content)
        ? raw.message.content.find((item) => item.type === "toolCall")?.name
        : null;
      return tool ? `工具调用准备中：${tool}` : "工具调用准备中";
    }
    return "模型回复更新中";
  }
  if (raw.type === "agent_start") {
    return "Agent 已启动";
  }
  if (raw.type === "turn_start") {
    return "开始处理本轮任务";
  }
  if (raw.type === "turn_end") {
    return "本轮任务处理完成";
  }
  if (raw.command) {
    return `${labelRuntimeCommand(raw.command)}${raw.success === false ? "失败" : ""}`;
  }
  if (raw.type) {
    return `运行事件：${raw.type}`;
  }
  const text = JSON.stringify(raw);
  return text.length > 220 ? `${text.slice(0, 220)}...` : text;
}

function summarizeWorkbenchStreamEvent(event) {
  const payload = event.payload || {};
  if (event.event_type === "assistant_delta") {
    return payload.delta || "收到回复片段";
  }
  if (event.event_type === "assistant_message") {
    return trimText(payload.text || "", 220) || "收到完整回复";
  }
  if (event.event_type === "tool_call") {
    const name = payload.tool_name || "工具";
    return `开始调用：${name}`;
  }
  if (event.event_type === "tool_result") {
    const name = payload.tool_name || "工具";
    if (payload.is_error) {
      const text = trimText(payload.text || payload.message || "", 180);
      return text ? `${name} 执行失败：${text}` : `${name} 执行失败`;
    }
    return `${name} 执行完成`;
  }
  if (event.event_type === "file_write") {
    const bytes = typeof payload.bytes === "number" ? ` · ${payload.bytes} bytes` : "";
    return `写入文件：${payload.path || "-"}${bytes}`;
  }
  if (event.event_type === "status") {
    const labels = {
      started: "Agent 已启动",
      completed: "Agent 已完成",
      turn_started: "开始处理本轮任务",
      turn_completed: "本轮任务处理完成",
      aborted: "已中止",
      prompt_accepted: "Prompt 已发送",
    };
    return labels[payload.status] || payload.status || "状态更新";
  }
  if (event.event_type === "artifact_candidate") {
    return `产物候选：${payload.artifact_id || payload.path || "-"}`;
  }
  if (event.event_type === "running_service") {
    return formatRunningServiceSummary(payload);
  }
  if (event.event_type === "error") {
    return payload.message || "执行出错";
  }
  return JSON.stringify(payload);
}

function formatRunningServiceSummary(payload) {
  const name = payload.service_name || "运行服务";
  const lines = [`${name} 已启动。`];
  if (payload.url) {
    lines.push(`地址：[${payload.url}](${payload.url})`);
  }
  if (payload.port) {
    lines.push(`端口：${payload.port}`);
  }
  if (payload.command) {
    lines.push(`命令：\`${payload.command}\``);
  }
  if (payload.cwd) {
    lines.push(`目录：\`${payload.cwd}\``);
  }
  return lines.join("\n\n");
}

function summaryCounts(summary) {
  if (!summary) {
    return "-";
  }
  const parts = [];
  if (typeof summary.output_count === "number") {
    parts.push(`输出 ${summary.output_count}`);
  }
  if (typeof summary.produced_count === "number") {
    parts.push(`已产出 ${summary.produced_count}`);
  }
  if (typeof summary.missing_count === "number") {
    parts.push(`缺失 ${summary.missing_count}`);
  }
  if (typeof summary.candidate_count === "number") {
    parts.push(`候选 ${summary.candidate_count}`);
  }
  if (typeof summary.skipped_count === "number") {
    parts.push(`跳过 ${summary.skipped_count}`);
  }
  return parts.join(" · ") || "-";
}

function buildDevShelfNextAction(detail) {
  const previews = pendingCandidatePreviewArtifacts();
  if (previews.length) {
    return buildPendingArtifactReviewAction(previews);
  }
  const gates = detail.pending_human_gates || [];
  if (gates.length) {
    return `等待人工确认：${gates.map((gate) => gate.label || labelArtifact(gate.artifact_id)).join("、")}。请先查看中间产物。`;
  }
  if (detail.status === "cancelled") {
    return "任务已终止，不会继续推进。";
  }
  if (detail.status === "completed") {
    return "本轮任务已完成。";
  }
  if (canRegisterImplementationResult(detail)) {
    return "下一步建议：登记执行结果，然后刷新进入后续阶段。";
  }
  const decision = packetDecision(detail);
  const reason = detail.router?.reason
    || detail.latest_packet?.content?.reason
    || detail.latest_packet?.content?.router_result?.reason;
  if (decision === "no_route") {
    return reason ? `当前暂无可推进步骤：${reason}` : "当前暂无可推进步骤。";
  }
  if (decision === "blocked") {
    return reason ? `当前流程被阻塞：${reason}` : "当前流程被阻塞。";
  }
  const target = packetTarget(detail);
  if (decision === "enter_stage") {
    return `下一步建议：${formatTarget(target)}。这是流程推进，不会启动 pi-agent。`;
  }
  if (decision === "run_manifest" && isExecutionContext(detail)) {
    return "下一步建议：执行代码实现";
  }
  if (target) {
    return `下一步建议：${formatTarget(target)}`;
  }
  if (detail.status === "ready_for_next_stage") {
    return "等待工作终端继续推进下一阶段。";
  }
  return "等待终端流程推进。";
}

function buildPendingArtifactReviewAction(previews) {
  return `请先查看并确认：${formatArtifactNames(previews)}`;
}

function buildPendingArtifactReviewSummary(previews) {
  return [
    "## 当前建议",
    "",
    `- 操作：${buildPendingArtifactReviewAction(previews)}`,
    "- 状态：等待人工确认",
    "- 说明：已有新生成的中间产物尚未确认，确认前不会继续启动下一步生成。",
  ].join("\n");
}

function formatArtifactNames(artifacts) {
  return artifacts
    .map((artifact) => artifact.title || labelArtifact(artifact.artifact_id))
    .filter(Boolean)
    .join("、") || "中间产物";
}

function buildPacketSummary(packet) {
  const content = packet.content || {};
  const lines = [
    "## 当前建议",
    "",
    `- 判断：${labelDecision(packet.decision_type)}`,
    `- 状态：${labelPacketReady(packet.ready)}`,
  ];
  const target = packet.target || content.target;
  if (target) {
    lines.push(`- 建议动作：${formatTarget(target)}`);
  }
  const reason = content.reason || content.router_result?.reason;
  if (reason) {
    lines.push(`- 说明：${reason}`);
  }
  const gates = content.pending_human_gates || content.router_result?.pending_human_gates || [];
  if (gates.length) {
    lines.push("", "## 等待确认", "");
    for (const gate of gates) {
      lines.push(`- ${gate.label || labelArtifact(gate.artifact_id)}`);
    }
  }
  if (content.stage) {
    lines.push("", "## 准备进入阶段", "", `- ${labelStage(content.stage)}`);
  }
  if (!target && !reason && !gates.length && !content.stage) {
    lines.push("", "暂无更多推进说明。");
  }
  return lines.join("\n");
}

function formatTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function renderMarkdown(source) {
  const escaped = escapeHtml(source || "");
  const lines = escaped.split("\n");
  const chunks = [];
  let inList = false;
  let inCode = false;
  let codeBuffer = [];
  let tableBuffer = [];

  function flushList() {
    if (inList) {
      chunks.push("</ul>");
      inList = false;
    }
  }

  function flushCode() {
    if (inCode) {
      chunks.push(`<pre><code>${codeBuffer.join("\n")}</code></pre>`);
      codeBuffer = [];
      inCode = false;
    }
  }

  function flushTable() {
    if (!tableBuffer.length) {
      return;
    }
    const rows = tableBuffer.map((line) => parseMarkdownTableCells(line));
    const hasHeader = rows.length >= 2 && isMarkdownTableSeparator(tableBuffer[1]);
    if (!hasHeader) {
      for (const row of rows) {
        chunks.push(`<p>${row.map(inlineMarkdown).join(" | ")}</p>`);
      }
      tableBuffer = [];
      return;
    }

    const header = rows[0];
    const body = rows.slice(2);
    const headerHtml = header.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("");
    const bodyHtml = body
      .map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`)
      .join("");
    chunks.push(`<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`);
    tableBuffer = [];
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      flushList();
      flushTable();
      if (inCode) {
        flushCode();
      } else {
        inCode = true;
      }
      continue;
    }

    if (inCode) {
      codeBuffer.push(line);
      continue;
    }

    if (!line.trim()) {
      flushList();
      flushTable();
      continue;
    }

    if (isMarkdownTableLine(line)) {
      flushList();
      tableBuffer.push(line);
      continue;
    }

    flushTable();

    if (line.startsWith("### ")) {
      flushList();
      chunks.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`);
      continue;
    }
    if (line.startsWith("## ")) {
      flushList();
      chunks.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`);
      continue;
    }
    if (line.startsWith("# ")) {
      flushList();
      chunks.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`);
      continue;
    }

    if (line.startsWith("- ") || line.startsWith("* ")) {
      if (!inList) {
        chunks.push("<ul>");
        inList = true;
      }
      chunks.push(`<li>${inlineMarkdown(line.slice(2))}</li>`);
      continue;
    }
    const orderedMatch = line.match(/^\d+\.\s+(.+)$/);
    if (orderedMatch) {
      if (!inList) {
        chunks.push("<ul>");
        inList = true;
      }
      chunks.push(`<li>${inlineMarkdown(orderedMatch[1])}</li>`);
      continue;
    }

    flushList();
    chunks.push(`<p>${inlineMarkdown(line)}</p>`);
  }

  flushList();
  flushTable();
  flushCode();

  return chunks.join("") || "<p>当前阶段还没有产物。</p>";
}

function isMarkdownTableLine(line) {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.split("|").length > 2;
}

function isMarkdownTableSeparator(line) {
  return parseMarkdownTableCells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
}

function parseMarkdownTableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function inlineMarkdown(line) {
  return line.replace(/`([^`]+)`/g, "<code>$1</code>");
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderModelConfigControls() {
  const config = state.devShelf.gateway.modelConfig;
  if (!config) {
    return;
  }
  renderProviderOptions(config);
  renderAccountOptions(config);
  renderModelOptions(config);
  renderProviderSpecificConfig(config);
}

function renderProviderOptions(config) {
  const currentProvider = elements.devShelfGatewayProviderControl.value || config.provider || "openai-codex";
  elements.devShelfGatewayProviderControl.innerHTML = "";
  for (const provider of config.providers || []) {
    const option = document.createElement("option");
    option.value = provider.provider;
    option.textContent = provider.label || provider.provider;
    elements.devShelfGatewayProviderControl.appendChild(option);
  }
  elements.devShelfGatewayProviderControl.value = providerOptionExists(currentProvider)
    ? currentProvider
    : (config.provider || "openai-codex");
}

function providerOptionExists(provider) {
  return Array.from(elements.devShelfGatewayProviderControl.options)
    .some((option) => option.value === provider);
}

function renderAccountOptions(config) {
  const provider = currentGatewayProvider();
  const providerConfig = gatewayProviderConfig(config, provider);
  elements.devShelfGatewayAccount.innerHTML = "";
  const accounts = providerConfig?.accounts?.length ? providerConfig.accounts : ["a"];
  for (const account of accounts) {
    const option = document.createElement("option");
    option.value = account;
    option.textContent = account === "default" ? "default / main" : account;
    elements.devShelfGatewayAccount.appendChild(option);
  }
  const desired = config.account || providerConfig?.default_account || accounts[0] || "";
  elements.devShelfGatewayAccount.value = accountOptionExists(desired) ? desired : (accounts[0] || "");
}

function accountOptionExists(account) {
  return Array.from(elements.devShelfGatewayAccount.options)
    .some((option) => option.value === account);
}

function renderModelOptions(config) {
  const provider = currentGatewayProvider();
  const models = state.devShelf.gateway.availableModels || [];
  const filtered = models.filter((model) => model.provider.toLowerCase() === provider.toLowerCase());
  elements.devShelfGatewayModelControl.innerHTML = "";
  const seen = new Set();
  for (const model of filtered) {
    if (seen.has(model.model)) {
      continue;
    }
    seen.add(model.model);
    const option = document.createElement("option");
    option.value = model.model;
    option.textContent = `${model.model} · ${model.context_window} / ${model.max_output}`;
    elements.devShelfGatewayModelControl.appendChild(option);
  }
  const providerConfig = gatewayProviderConfig(config, provider);
  const desired = (
    provider === config.provider
      ? config.model
      : providerConfig?.default_model
  ) || defaultGatewayModel(provider);
  if (!modelOptionExists(desired)) {
    const fallback = document.createElement("option");
    fallback.value = desired;
    fallback.textContent = desired;
    elements.devShelfGatewayModelControl.appendChild(fallback);
  }
  elements.devShelfGatewayModelControl.value = desired;
}

function modelOptionExists(model) {
  return Array.from(elements.devShelfGatewayModelControl.options)
    .some((option) => option.value === model);
}

function renderProviderSpecificConfig(config) {
  const provider = currentGatewayProvider();
  const providerConfig = gatewayProviderConfig(config, provider);
  const isCodex = provider === "openai-codex";
  elements.devShelfGatewayAccountRow.classList.toggle("hidden", !isCodex);
  const authConfigured = Boolean(providerConfig?.auth_configured);
  elements.devShelfGatewayAuthStatus.textContent = authConfigured ? "已配置" : "未配置";
  elements.devShelfGatewayAuthStatus.className = `badge ${authConfigured ? "ready" : "subtle"} mini-badge`;
  elements.devShelfGatewayAuthSource.textContent = providerConfig?.auth_source
    ? `来自 ${providerConfig.auth_source}`
    : "未找到 pi auth.json 认证。";
  elements.devShelfGatewayModelConfigStatus.textContent = modelConfigStatusText(provider, providerConfig);
  elements.devShelfGatewayModelConfigStatus.className = "minor-meta";
}

function modelConfigStatusText(provider, providerConfig) {
  if (provider === "deepseek") {
    return providerConfig?.auth_configured
      ? "DeepSeek 使用 pi auth.json 中的认证启动。"
      : "DeepSeek 未在 pi auth.json 中配置认证。";
  }
  return providerConfig?.auth_configured
    ? "Codex 使用本机 pi 账号和所选模型启动。"
    : "当前 Codex 账号未检测到 pi auth.json 认证。";
}

function renderArtifactAction(artifact) {
  const artifactAction = state.devShelf.artifactAction;
  const canConfirm = Boolean(artifact?.source === "gateway_candidate" && artifact?.candidate_id);
  const canRevise = canReviseArtifactFromReview(artifact);
  const showFeedback = Boolean(artifactAction.feedbackVisible);
  elements.devShelfArtifactActions.classList.toggle(
    "hidden",
    !canConfirm && !canRevise && !artifactAction.error && !artifactAction.message,
  );
  elements.devShelfArtifactConfirmButton.disabled = !canConfirm || artifactAction.busy;
  elements.devShelfArtifactConfirmButton.textContent = artifactAction.busy ? "确认中" : "确认产物";
  elements.devShelfArtifactConfirmButton.classList.toggle("hidden", !canConfirm);
  elements.devShelfArtifactReviseToggleButton.disabled = !canRevise || artifactAction.busy;
  elements.devShelfArtifactReviseToggleButton.classList.toggle("hidden", !canRevise);
  elements.devShelfArtifactReviseToggleButton.textContent = showFeedback ? "收起修改意见" : "提出修改意见";
  elements.devShelfArtifactReviseToggleButton.setAttribute("aria-expanded", showFeedback ? "true" : "false");
  elements.devShelfArtifactFeedbackRow.classList.toggle("hidden", !canRevise || !showFeedback);
  elements.devShelfArtifactFeedbackInput.disabled = !canRevise || artifactAction.busy;
  elements.devShelfArtifactReviseSubmitButton.disabled = !canRevise || artifactAction.busy || !showFeedback;
  elements.devShelfArtifactReviseSubmitButton.classList.toggle("hidden", !canRevise || !showFeedback);
  elements.devShelfArtifactReviseSubmitButton.textContent = artifactAction.busy ? "提交中" : "提交修改意见并重新生成";

  if (artifactAction.error) {
    elements.devShelfArtifactActionStatus.textContent = artifactAction.error;
    elements.devShelfArtifactActionStatus.className = "artifact-action-status error";
    return;
  }
  if (artifactAction.message) {
    elements.devShelfArtifactActionStatus.textContent = artifactAction.message;
    elements.devShelfArtifactActionStatus.className = "artifact-action-status";
    return;
  }
  if (canConfirm && canRevise) {
    elements.devShelfArtifactActionStatus.textContent = "确认或提交修改意见都会绑定当前产物。";
  } else if (canConfirm) {
    elements.devShelfArtifactActionStatus.textContent = "确认后会刷新下一步。";
  } else if (canRevise) {
    elements.devShelfArtifactActionStatus.textContent = "修改意见会绑定当前产物并用于重新生成。";
  } else {
    elements.devShelfArtifactActionStatus.textContent = "";
  }
  elements.devShelfArtifactActionStatus.className = "artifact-action-status";
}

export { elements, AUTO_REFRESH_INTERVAL_MS, SCROLL_BOTTOM_TOLERANCE_PX, GATEWAY_STREAM_EVENT_TYPES, labelStage, labelRunStatus, labelDecision, labelArtifactStatus, labelGatewayStatus, renderMarkdown, renderDevShelfPacket, formatTime, escapeHtml, buildDevShelfNextAction, summarizeWorkbenchStreamEvent, renderProviderSpecificConfig, renderModelConfigControls, renderProviderOptions, renderAccountOptions, renderModelOptions, modelConfigStatusText, currentGatewayProvider, gatewayProviderConfig, defaultGatewayModel, renderArtifactAction };
export { devShelfStageLabels, runStatusLabels, decisionLabels, artifactStatusLabels, artifactLabels, targetLabels };