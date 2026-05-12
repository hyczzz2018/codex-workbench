const state = {
  devShelf: {
    runs: [],
    selectedRunId: null,
    detail: null,
    loading: false,
    error: null,
    backendHealth: {
      status: "checking",
      message: "后端连接检测中。",
      logPath: null,
      checkedAt: null,
    },
    selectedArtifactId: null,
    artifactPreviewOpen: false,
    artifactPreviewPageScroll: null,
    artifactPreviewScroll: {
      key: null,
      scrollTop: 0,
      nearBottom: true,
    },
    flowDocumentsOpen: null,
    renderingFlowDocuments: false,
    polling: false,
    autoRefreshInFlight: false,
    autoRefreshError: null,
    lastRefreshedAt: null,
    createRun: {
      visible: false,
      busy: false,
      message: null,
      error: null,
      pathMessage: null,
      lastRunId: null,
    },
    runAction: {
      busy: false,
      message: null,
      error: null,
    },
    artifactAction: {
      busy: false,
      message: null,
      error: null,
      feedbackVisible: false,
    },
    collabFeedback: {
      message: null,
      error: null,
    },
    collabScroll: {
      contextKey: null,
      userDetached: false,
      messageSignature: null,
      hasNewMessages: false,
    },
    collabTypewriter: {
      contextKey: null,
      entries: {},
    },
    dirPicker: {
      open: false,
      loading: false,
      rootPath: null,
      currentPath: null,
      parentPath: null,
      entries: [],
      error: null,
    },
    gateway: {
      status: null,
      events: [],
      result: null,
      candidates: null,
      transcript: null,
      liveAssistantMessageKey: null,
      activeView: "chat",
      panelOpen: null,
      cursor: 0,
      sessionId: null,
      availableModels: [],
      modelConfig: null,
      streamConnected: false,
      streamError: null,
      error: null,
      renderingPanel: false,
      control: {
        busy: false,
        message: null,
        error: null,
        lastResponse: null,
      },
    },
  },
};

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

async function loadModelConfig() {
  try {
    const response = await fetch("/api/dev-shelf/model-config", { cache: "no-store" });
    const data = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(data.detail || "读取模型配置失败");
    }
    state.devShelf.gateway.modelConfig = data;
    renderModelConfigControls();
  } catch (error) {
    elements.devShelfGatewayModelConfigStatus.textContent = error.message;
    elements.devShelfGatewayModelConfigStatus.className = "minor-meta error";
  }
}

async function loadAvailableModels() {
  const button = elements.devShelfGatewayRefreshModelsButton;
  button.disabled = true;
  button.textContent = "加载中";
  try {
    const response = await fetch("/api/dev-shelf/models");
    const data = await readJsonResponse(response);
    if (response.ok && data.models) {
      state.devShelf.gateway.availableModels = data.models;
      renderModelConfigControls();
    }
  } catch (error) {
    // non-blocking; keep existing suggestions
  } finally {
    button.disabled = false;
    button.textContent = "刷新";
  }
}

async function loadBackendHealth({ silent = false } = {}) {
  if (!silent) {
    state.devShelf.backendHealth.status = "checking";
    state.devShelf.backendHealth.message = "后端连接检测中。";
    renderBackendHealthStatus();
  }
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const payload = await readJsonResponse(response);
    if (!response.ok || payload.status !== "ok") {
      throw new Error(payload.detail || "后端健康检查失败");
    }
    state.devShelf.backendHealth = {
      status: "ok",
      message: `后端已连接 · 日志 ${payload.log_path || "/tmp/codex-workbench.log"}`,
      logPath: payload.log_path || null,
      checkedAt: new Date(),
    };
  } catch (error) {
    markBackendHealthUnavailable(error);
  } finally {
    renderBackendHealthStatus();
  }
}

function markBackendHealthUnavailable(error) {
  state.devShelf.backendHealth = {
    status: "offline",
    message: `后端连接失败：${error?.message || "请检查服务和端口"}。日志 /tmp/codex-workbench.log`,
    logPath: "/tmp/codex-workbench.log",
    checkedAt: new Date(),
  };
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

function currentGatewayProvider() {
  return elements.devShelfGatewayProviderControl.value || state.devShelf.gateway.modelConfig?.provider || "openai-codex";
}

function gatewayProviderConfig(config, provider) {
  return (config?.providers || []).find((item) => item.provider === provider) || null;
}

function defaultGatewayModel(provider) {
  return provider === "deepseek" ? "deepseek-v4-pro" : "gpt-5.4";
}

function handleGatewayProviderChange() {
  renderModelConfigControls();
  saveModelConfigSelection();
}

async function saveModelConfigSelection() {
  const provider = currentGatewayProvider();
  try {
    const response = await fetch("/api/dev-shelf/model-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider,
        model: elements.devShelfGatewayModelControl.value || defaultGatewayModel(provider),
        account: provider === "openai-codex" ? (elements.devShelfGatewayAccount.value || null) : null,
      }),
    });
    const data = await readJsonResponse(response);
    if (response.ok) {
      state.devShelf.gateway.modelConfig = data;
      renderModelConfigControls();
    }
  } catch (error) {
    elements.devShelfGatewayModelConfigStatus.textContent = error.message;
  }
}

async function loadDevShelfRuns() {
  await refreshDevShelfSnapshot({ silent: false });
}

async function createDevShelfRun(event) {
  event.preventDefault();
  const createRun = state.devShelf.createRun;
  const projectName = elements.projectNameInput.value.trim();
  const requirement = elements.projectRequirementInput.value.trim();
  if (!projectName || !requirement) {
    createRun.error = "项目名和需求都要填写。";
    createRun.message = null;
    renderDevShelf();
    return;
  }

  createRun.busy = true;
  createRun.error = null;
  createRun.message = null;
  createRun.pathMessage = null;
  renderDevShelf();

  try {
    const payload = {
      project_name: projectName,
      requirement,
      task_type: elements.projectTaskTypeInput.value,
      project_context: elements.projectContextInput.value,
      project_path: elements.projectPathInput.value.trim() || null,
      workspace_confirmed: elements.projectWorkspaceConfirmedInput.checked,
      allow_create_project_dir: elements.projectContextInput.value === "new_project",
    };
    const response = await fetch("/api/dev-shelf/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "创建 run 失败");
    }
    createRun.lastRunId = result.run_id;
    createRun.message = result.message || `已创建 ${result.run_id}`;
    createRun.visible = false;
    state.devShelf.selectedRunId = result.run_id;
    state.devShelf.selectedArtifactId = null;
    resetArtifactPreviewState();
    state.devShelf.artifactAction = defaultArtifactActionState();
    state.devShelf.collabFeedback = { message: null, error: null };
    resetGatewayState();
    await refreshDevShelfSnapshot({ silent: false });
  } catch (error) {
    createRun.error = error.message;
  } finally {
    createRun.busy = false;
    renderDevShelf();
  }
}

async function openProjectDirectoryPicker() {
  const dirPicker = state.devShelf.dirPicker;
  dirPicker.open = true;
  await loadProjectDirectories(elements.projectPathInput.value.trim() || null);
}

function closeProjectDirectoryPicker() {
  state.devShelf.dirPicker.open = false;
  renderDirectoryPicker();
}

function setGatewayView(view) {
  if (!["chat", "events", "summary"].includes(view)) {
    return;
  }
  state.devShelf.gateway.activeView = view;
  renderDevShelfGateway(state.devShelf.detail);
}

function showProjectCreatePanel() {
  const createRun = state.devShelf.createRun;
  createRun.visible = true;
  createRun.busy = false;
  createRun.message = null;
  createRun.error = null;
  createRun.pathMessage = null;
  state.devShelf.selectedRunId = null;
  state.devShelf.detail = null;
  state.devShelf.selectedArtifactId = null;
  resetArtifactPreviewState();
  state.devShelf.flowDocumentsOpen = null;
  state.devShelf.artifactAction = defaultArtifactActionState();
  state.devShelf.collabFeedback = { message: null, error: null };
  state.devShelf.runAction = { busy: false, message: null, error: null };
  resetGatewayState({ preserveControl: true });
  elements.projectCreateForm.reset();
  renderDevShelf();
}

async function loadProjectDirectories(path) {
  const dirPicker = state.devShelf.dirPicker;
  dirPicker.loading = true;
  dirPicker.error = null;
  renderDirectoryPicker();

  try {
    const params = new URLSearchParams();
    if (path) {
      params.set("path", path);
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const response = await fetch(`/api/dev-shelf/directories${suffix}`, { cache: "no-store" });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "读取目录失败");
    }
    dirPicker.rootPath = result.root_path;
    dirPicker.currentPath = result.current_path;
    dirPicker.parentPath = result.parent_path;
    dirPicker.entries = result.items || [];
  } catch (error) {
    dirPicker.error = error.message;
  } finally {
    dirPicker.loading = false;
    renderDirectoryPicker();
  }
}

async function createProjectDirectory() {
  const dirPicker = state.devShelf.dirPicker;
  const name = elements.directoryNewNameInput.value.trim();
  if (!name) {
    dirPicker.error = "请输入新目录名。";
    renderDirectoryPicker();
    return;
  }

  dirPicker.loading = true;
  dirPicker.error = null;
  renderDirectoryPicker();

  try {
    const response = await fetch("/api/dev-shelf/directories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parent_path: dirPicker.currentPath,
        name,
      }),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "新建目录失败");
    }
    elements.directoryNewNameInput.value = "";
    await loadProjectDirectories(result.path);
  } catch (error) {
    dirPicker.error = error.message;
  } finally {
    dirPicker.loading = false;
    renderDirectoryPicker();
  }
}

function chooseCurrentProjectDirectory() {
  const dirPicker = state.devShelf.dirPicker;
  if (!dirPicker.currentPath) {
    return;
  }
  elements.projectPathInput.value = dirPicker.currentPath;
  elements.projectWorkspaceConfirmedInput.checked = true;
  state.devShelf.createRun.pathMessage = "已选择项目目录，并默认确认该路径可读写。";
  state.devShelf.createRun.error = null;
  closeProjectDirectoryPicker();
  renderDevShelf();
}

async function cancelDevShelfRun() {
  const runId = state.devShelf.selectedRunId;
  if (!runId) {
    return;
  }
  const runAction = state.devShelf.runAction;
  runAction.busy = true;
  runAction.message = null;
  runAction.error = null;
  renderDevShelf();

  try {
    const response = await fetch(`/api/dev-shelf/runs/${runId}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: "用户在网页终止任务。" }),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "终止任务失败");
    }
    state.devShelf.detail = result;
    runAction.message = "任务已标记为已取消。";
    await refreshDevShelfSnapshot({ silent: false });
  } catch (error) {
    runAction.error = error.message;
  } finally {
    runAction.busy = false;
    renderDevShelf({ preservePageScroll: true });
  }
}

async function handlePrimaryDevShelfAction() {
  const action = primaryActionForDetail(state.devShelf.detail);
  if (!action) {
    return;
  }
  if (action.type === "workflow_continue") {
    await continueDevShelfWorkflow();
    return;
  }
  if (action.type === "register_result") {
    await registerDevShelfGatewayResult();
    return;
  }
  await startDevShelfGateway();
}

async function continueDevShelfWorkflow() {
  const runId = state.devShelf.selectedRunId;
  if (!runId) {
    return;
  }
  const runAction = state.devShelf.runAction;
  runAction.busy = true;
  runAction.message = null;
  runAction.error = null;
  renderDevShelf();

  try {
    const response = await fetch(`/api/dev-shelf/runs/${runId}/workflow/continue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "继续流程失败");
    }
    state.devShelf.detail = result;
    runAction.message = "已继续流程。";
    await refreshDevShelfSnapshot({ silent: false });
  } catch (error) {
    runAction.error = error.message;
  } finally {
    runAction.busy = false;
    renderDevShelf();
  }
}

async function confirmSelectedGatewayArtifact() {
  const runId = state.devShelf.selectedRunId;
  const artifact = visibleDevShelfArtifacts()
    .find((item) => item.artifact_id === state.devShelf.selectedArtifactId);
  if (!runId || !artifact?.candidate_id) {
    return;
  }

  const artifactAction = state.devShelf.artifactAction;
  artifactAction.busy = true;
  artifactAction.message = null;
  artifactAction.error = null;
  renderDevShelf();

  try {
    const response = await fetch(
      `/api/dev-shelf/runs/${runId}/gateway/candidates/${encodeURIComponent(artifact.candidate_id)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.devShelf.gateway.sessionId,
          decision_note: `${artifact.title || labelArtifact(artifact.artifact_id)} 已在网页确认。`,
        }),
      },
    );
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "确认产物失败");
    }
    state.devShelf.detail = result;
    state.devShelf.selectedArtifactId = artifact.artifact_id;
    artifactAction.message = "已确认，正在刷新下一步。";
    await refreshDevShelfSnapshot({ silent: false });
    artifactAction.message = null;
  } catch (error) {
    artifactAction.error = error.message;
  } finally {
    artifactAction.busy = false;
    renderDevShelf();
  }
}

function toggleArtifactFeedbackInput() {
  const artifactAction = state.devShelf.artifactAction;
  artifactAction.feedbackVisible = !artifactAction.feedbackVisible;
  artifactAction.message = null;
  artifactAction.error = null;
  renderArtifactAction(selectedDevShelfArtifact());
  if (artifactAction.feedbackVisible) {
    window.requestAnimationFrame(() => elements.devShelfArtifactFeedbackInput.focus());
  }
}

async function submitArtifactRevisionFromReview() {
  const artifactAction = state.devShelf.artifactAction;
  const feedbackText = elements.devShelfArtifactFeedbackInput.value.trim();
  if (!selectedDevShelfArtifact()) {
    artifactAction.error = "请先选择要修改的产物。";
    renderArtifactAction(null);
    return;
  }
  if (!feedbackText) {
    artifactAction.feedbackVisible = true;
    artifactAction.error = "请先输入修改意见。";
    renderArtifactAction(selectedDevShelfArtifact());
    return;
  }
  if (canAbortGateway()) {
    artifactAction.error = "pi-agent 正在运行，请先中止后再提交产物修改意见。";
    renderArtifactAction(selectedDevShelfArtifact());
    return;
  }

  artifactAction.busy = true;
  artifactAction.error = null;
  artifactAction.message = "正在记录修改意见。";
  renderArtifactAction(selectedDevShelfArtifact());

  try {
    const detail = await rejectSelectedArtifactForRevision(feedbackText);
    state.devShelf.detail = detail;
    elements.devShelfArtifactFeedbackInput.value = "";
    artifactAction.feedbackVisible = false;
    artifactAction.message = "已记录修改意见，正在启动重新生成。";
    await refreshDevShelfSnapshot({ silent: true });
    await startDevShelfGateway();
  } catch (error) {
    artifactAction.error = error.message;
    artifactAction.message = null;
  } finally {
    artifactAction.busy = false;
    renderDevShelf();
  }
}

function startDevShelfAutoRefresh() {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer);
  }
  state.devShelf.polling = true;
  autoRefreshTimer = window.setInterval(() => {
    loadBackendHealth({ silent: true });
    refreshDevShelfSnapshot({ silent: true });
  }, AUTO_REFRESH_INTERVAL_MS);
  renderDevShelf();
}

async function refreshDevShelfSnapshot({ silent } = {}) {
  if (silent && state.devShelf.autoRefreshInFlight) {
    return;
  }

  if (silent) {
    state.devShelf.autoRefreshInFlight = true;
  } else {
    state.devShelf.loading = true;
    state.devShelf.error = null;
  }
  renderDevShelf({ preservePageScroll: silent });

  try {
    const response = await fetch("/api/dev-shelf/runs", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("读取 dev-shelf 任务失败");
    }
    const payload = await response.json();
    const runs = payload.items || [];
    state.devShelf.runs = runs;

    let nextRunId = state.devShelf.selectedRunId;
    if (nextRunId && !runs.some((item) => item.run_id === nextRunId)) {
      nextRunId = runs[0]?.run_id || null;
      state.devShelf.detail = null;
      state.devShelf.selectedArtifactId = null;
    }
    if (!nextRunId && runs.length && !state.devShelf.createRun.visible) {
      nextRunId = runs[0].run_id;
    }

    state.devShelf.selectedRunId = nextRunId;
    if (nextRunId) {
      await loadDevShelfRunDetail(nextRunId);
    } else {
      state.devShelf.detail = null;
      state.devShelf.selectedArtifactId = null;
      resetGatewayState();
    }
    state.devShelf.lastRefreshedAt = new Date();
    state.devShelf.autoRefreshError = null;
  } catch (error) {
    markBackendHealthUnavailable(error);
    if (silent) {
      state.devShelf.autoRefreshError = error.message;
    } else {
      state.devShelf.error = error.message;
    }
  } finally {
    if (silent) {
      state.devShelf.autoRefreshInFlight = false;
    } else {
      state.devShelf.loading = false;
    }
    renderDevShelf({ preservePageScroll: silent });
  }
}

async function loadDevShelfRunDetail(runId) {
  state.devShelf.error = null;
  const response = await fetch(`/api/dev-shelf/runs/${runId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("读取 dev-shelf run 详情失败");
  }
  state.devShelf.selectedRunId = runId;
  state.devShelf.detail = await response.json();
  await loadGatewaySnapshot(runId);
  ensureSelectedArtifact();
}

function defaultGatewayControlState() {
  return {
    busy: false,
    message: null,
    error: null,
    lastResponse: null,
  };
}

function defaultArtifactActionState() {
  return {
    busy: false,
    message: null,
    error: null,
    feedbackVisible: false,
  };
}

function defaultArtifactPreviewScrollState() {
  return {
    key: null,
    scrollTop: 0,
    nearBottom: true,
  };
}

function resetArtifactPreviewState() {
  state.devShelf.artifactPreviewOpen = false;
  state.devShelf.artifactPreviewPageScroll = null;
  state.devShelf.artifactPreviewScroll = defaultArtifactPreviewScrollState();
}

function resetGatewayState({ preserveControl } = {}) {
  closeGatewayEventStream();
  clearGatewayArtifactSnapshotRefresh();
  const control = preserveControl
    ? (state.devShelf.gateway.control || defaultGatewayControlState())
    : defaultGatewayControlState();
  const panelOpen = preserveControl ? state.devShelf.gateway.panelOpen : null;
  const availableModels = state.devShelf.gateway.availableModels || [];
  const modelConfig = state.devShelf.gateway.modelConfig;
  state.devShelf.gateway = {
    status: null,
    events: [],
    result: null,
    candidates: null,
    transcript: null,
    liveAssistantMessageKey: null,
    activeView: state.devShelf.gateway.activeView || "chat",
    panelOpen,
    cursor: 0,
    sessionId: null,
    availableModels,
    modelConfig,
    streamConnected: false,
    streamError: null,
    error: null,
    renderingPanel: false,
    control,
  };
}

async function loadGatewaySnapshot(runId) {
  try {
    const statusResponse = await fetch(`/api/dev-shelf/runs/${runId}/gateway/latest`, { cache: "no-store" });
    if (statusResponse.status === 404) {
      resetGatewayState({ preserveControl: true });
      state.devShelf.gateway.error = "暂无 Gateway session。";
      return;
    }
    if (!statusResponse.ok) {
      throw new Error("读取 Gateway 状态失败");
    }
    const status = await statusResponse.json();
    const sessionChanged = status.gateway_session_id !== state.devShelf.gateway.sessionId;
    if (sessionChanged) {
      closeGatewayEventStream();
      state.devShelf.gateway.events = [];
      state.devShelf.gateway.cursor = 0;
      state.devShelf.gateway.result = null;
      state.devShelf.gateway.candidates = null;
      state.devShelf.gateway.transcript = null;
      state.devShelf.gateway.liveAssistantMessageKey = null;
      state.devShelf.gateway.panelOpen = null;
    }
    state.devShelf.gateway.status = status;
    state.devShelf.gateway.sessionId = status.gateway_session_id;
    state.devShelf.gateway.error = null;
    if (status.status === "failed") {
      state.devShelf.gateway.control.message = null;
      state.devShelf.gateway.control.error = status.error || "Gateway 启动失败。";
    }
    if (!status.gateway_session_id) {
      state.devShelf.gateway.events = [];
      state.devShelf.gateway.cursor = 0;
      state.devShelf.gateway.result = null;
      state.devShelf.gateway.candidates = null;
      state.devShelf.gateway.transcript = null;
      state.devShelf.gateway.liveAssistantMessageKey = null;
      closeGatewayEventStream();
      return;
    }

    const params = new URLSearchParams({
      cursor: String(state.devShelf.gateway.cursor || 0),
      limit: "50",
    });
    params.set("session_id", status.gateway_session_id);
    const streamActive = shouldUseGatewayEventStream(status)
      ? ensureGatewayEventStream(runId, status.gateway_session_id)
      : false;
    if (!streamActive) {
      closeGatewayEventStream();
    }
    const eventsRequest = streamActive && !state.devShelf.gateway.streamError
      ? Promise.resolve(null)
      : fetch(`/api/dev-shelf/runs/${runId}/gateway/events?${params.toString()}`, { cache: "no-store" });
    const [eventsResponse, transcriptResponse, resultResponse, candidatesResponse] = await Promise.all([
      eventsRequest,
      fetch(`/api/dev-shelf/runs/${runId}/gateway/transcript?session_id=${encodeURIComponent(status.gateway_session_id)}`, {
        cache: "no-store",
      }),
      fetch(`/api/dev-shelf/runs/${runId}/gateway/result?session_id=${encodeURIComponent(status.gateway_session_id)}`, {
        cache: "no-store",
      }),
      fetch(`/api/dev-shelf/runs/${runId}/gateway/candidates?session_id=${encodeURIComponent(status.gateway_session_id)}`, {
        cache: "no-store",
      }),
    ]);

    if (eventsResponse?.ok) {
      const page = await eventsResponse.json();
      state.devShelf.gateway.events = [...state.devShelf.gateway.events, ...(page.events || [])].slice(-120);
      state.devShelf.gateway.cursor = page.next_cursor || state.devShelf.gateway.cursor;
    }
    if (transcriptResponse.ok) {
      state.devShelf.gateway.transcript = mergeGatewayTranscript(
        state.devShelf.gateway.transcript,
        await transcriptResponse.json(),
      );
    }
    if (resultResponse.ok) {
      state.devShelf.gateway.result = await resultResponse.json();
    }
    if (candidatesResponse.ok) {
      state.devShelf.gateway.candidates = await candidatesResponse.json();
    }
    ensureSelectedArtifact();
  } catch (error) {
    state.devShelf.gateway.error = error.message;
  }
}

function shouldUseGatewayEventStream(status) {
  return Boolean(
    status?.gateway_session_id
    && status.status !== "completed"
    && status.status !== "failed"
  );
}

function ensureGatewayEventStream(runId, sessionId) {
  if (typeof EventSource === "undefined" || !runId || !sessionId) {
    return false;
  }
  if (
    gatewayEventSource
    && gatewayStreamRunId === runId
    && gatewayStreamSessionId === sessionId
  ) {
    if (gatewayEventSource.readyState === EventSource.CLOSED) {
      closeGatewayEventStream({ clearError: false });
    } else {
      return true;
    }
  }

  if (
    gatewayEventSource
    && (gatewayStreamRunId !== runId || gatewayStreamSessionId !== sessionId)
  ) {
    closeGatewayEventStream();
  }

  if (
    gatewayEventSource
    && gatewayStreamRunId === runId
    && gatewayStreamSessionId === sessionId
  ) {
    return true;
  }

  closeGatewayEventStream();
  gatewayStreamRunId = runId;
  gatewayStreamSessionId = sessionId;
  state.devShelf.gateway.streamConnected = false;
  state.devShelf.gateway.streamError = null;

  const params = new URLSearchParams({
    session_id: sessionId,
    cursor: String(state.devShelf.gateway.cursor || 0),
    limit: "50",
  });
  const source = new EventSource(`/api/dev-shelf/runs/${runId}/gateway/stream?${params.toString()}`);
  gatewayEventSource = source;

  source.onopen = () => {
    if (!isActiveGatewayEventStream(source, runId, sessionId)) {
      return;
    }
    state.devShelf.gateway.streamConnected = true;
    state.devShelf.gateway.streamError = null;
    scheduleGatewayRender();
  };

  source.onerror = () => {
    if (!isActiveGatewayEventStream(source, runId, sessionId)) {
      return;
    }
    state.devShelf.gateway.streamConnected = false;
    state.devShelf.gateway.streamError = "实时事件连接中断，正在使用轮询刷新。";
    scheduleGatewayRender();
  };

  for (const eventType of GATEWAY_STREAM_EVENT_TYPES) {
    source.addEventListener(eventType, (message) => {
      handleGatewayStreamEvent(source, runId, sessionId, eventType, message);
    });
  }
  return true;
}

function closeGatewayEventStream({ clearError = true } = {}) {
  if (gatewayEventSource) {
    gatewayEventSource.close();
  }
  gatewayEventSource = null;
  gatewayStreamRunId = null;
  gatewayStreamSessionId = null;
  if (state.devShelf.gateway) {
    state.devShelf.gateway.streamConnected = false;
    if (clearError) {
      state.devShelf.gateway.streamError = null;
    }
  }
}

function clearGatewayArtifactSnapshotRefresh() {
  if (gatewayArtifactRefreshTimer !== null) {
    window.clearTimeout(gatewayArtifactRefreshTimer);
    gatewayArtifactRefreshTimer = null;
  }
  gatewayArtifactRefreshAttempts = 0;
  gatewayArtifactRefreshInFlight = false;
}

function scheduleGatewayArtifactSnapshotRefresh({
  delay = GATEWAY_ARTIFACT_REFRESH_DELAY_MS,
  expectCandidates = false,
  resetAttempts = false,
} = {}) {
  if (!state.devShelf.selectedRunId || !state.devShelf.gateway.sessionId) {
    return;
  }
  if (resetAttempts) {
    gatewayArtifactRefreshAttempts = 0;
  }
  if (gatewayArtifactRefreshTimer !== null) {
    window.clearTimeout(gatewayArtifactRefreshTimer);
  }
  gatewayArtifactRefreshTimer = window.setTimeout(() => {
    gatewayArtifactRefreshTimer = null;
    refreshGatewayArtifactSnapshots({ expectCandidates });
  }, delay);
}

async function refreshGatewayArtifactSnapshots({ expectCandidates = false } = {}) {
  if (gatewayArtifactRefreshInFlight) {
    return;
  }
  const runId = state.devShelf.selectedRunId;
  const sessionId = state.devShelf.gateway.sessionId;
  if (!runId || !sessionId) {
    return;
  }

  gatewayArtifactRefreshInFlight = true;
  try {
    const encodedSession = encodeURIComponent(sessionId);
    const [resultResponse, candidatesResponse] = await Promise.all([
      fetch(`/api/dev-shelf/runs/${runId}/gateway/result?session_id=${encodedSession}`, { cache: "no-store" }),
      fetch(`/api/dev-shelf/runs/${runId}/gateway/candidates?session_id=${encodedSession}`, { cache: "no-store" }),
    ]);

    if (resultResponse.ok) {
      state.devShelf.gateway.result = await resultResponse.json();
    }
    if (candidatesResponse.ok) {
      state.devShelf.gateway.candidates = await candidatesResponse.json();
    }
    ensureSelectedArtifact();

    const previews = candidatePreviewArtifacts();
    const hasSettledPreview = previews.some((item) => !item.live_pending);
    if (expectCandidates && !hasSettledPreview && gatewayArtifactRefreshAttempts < GATEWAY_ARTIFACT_REFRESH_MAX_ATTEMPTS) {
      gatewayArtifactRefreshAttempts += 1;
      scheduleGatewayArtifactSnapshotRefresh({
        delay: GATEWAY_ARTIFACT_REFRESH_RETRY_MS,
        expectCandidates: true,
      });
    } else if (!expectCandidates || hasSettledPreview) {
      gatewayArtifactRefreshAttempts = 0;
    }
  } catch (error) {
    state.devShelf.gateway.error = error.message;
    if (expectCandidates && gatewayArtifactRefreshAttempts < GATEWAY_ARTIFACT_REFRESH_MAX_ATTEMPTS) {
      gatewayArtifactRefreshAttempts += 1;
      scheduleGatewayArtifactSnapshotRefresh({
        delay: GATEWAY_ARTIFACT_REFRESH_RETRY_MS,
        expectCandidates: true,
      });
    }
  } finally {
    gatewayArtifactRefreshInFlight = false;
    renderDevShelf({ preservePageScroll: true });
  }
}

function isActiveGatewayEventStream(source, runId, sessionId) {
  return source === gatewayEventSource
    && state.devShelf.selectedRunId === runId
    && state.devShelf.gateway.sessionId === sessionId;
}

function handleGatewayStreamEvent(source, runId, sessionId, eventType, message) {
  if (!isActiveGatewayEventStream(source, runId, sessionId)) {
    return;
  }
  let event;
  try {
    event = JSON.parse(message.data);
  } catch (error) {
    state.devShelf.gateway.streamError = "实时事件解析失败。";
    scheduleGatewayRender();
    return;
  }
  if (!event || typeof event !== "object") {
    return;
  }
  event.event_type = event.event_type || eventType;
  appendGatewayStreamEvent(event);
  applyGatewayStreamEvent(event);
  updateGatewayCursor(event);
  state.devShelf.gateway.streamConnected = true;
  state.devShelf.gateway.streamError = null;

  if (isTerminalGatewayStreamEvent(event)) {
    closeGatewayEventStream();
  }
  scheduleGatewayRender();
}

function appendGatewayStreamEvent(event) {
  const events = state.devShelf.gateway.events || [];
  if (event.event_id && events.some((item) => item.event_id === event.event_id)) {
    return;
  }
  state.devShelf.gateway.events = [...events, event].slice(-120);
}

function updateGatewayCursor(event) {
  const cursor = Number.isInteger(event.cursor) ? event.cursor : event.runtime_sequence;
  if (Number.isInteger(cursor)) {
    state.devShelf.gateway.cursor = Math.max(state.devShelf.gateway.cursor || 0, cursor);
  }
}

function isTerminalGatewayStreamEvent(event) {
  if (event.event_type !== "status") {
    return false;
  }
  const status = event.payload?.status;
  return status === "completed" || status === "aborted";
}

function scheduleGatewayRender() {
  if (gatewayRenderTimer !== null) {
    return;
  }
  gatewayRenderTimer = window.setTimeout(() => {
    gatewayRenderTimer = null;
    renderDevShelf({ preservePageScroll: true });
  }, 16);
}

function applyGatewayStreamEvent(event) {
  const payload = event.payload || {};
  if (event.event_type === "assistant_delta" && payload.delta) {
    appendGatewayAssistantDelta(event, payload.delta);
    return;
  }
  if (event.event_type === "assistant_message" && payload.text) {
    upsertGatewayAssistantMessage(event, payload.text);
    return;
  }
  if (event.event_type === "tool_call") {
    appendGatewayTranscriptEvent(event, "tool", "tool", summarizeWorkbenchStreamEvent(event));
    return;
  }
  if (event.event_type === "tool_result") {
    appendGatewayTranscriptEvent(event, payload.is_error ? "error" : "tool", "tool", summarizeWorkbenchStreamEvent(event));
    return;
  }
  if (event.event_type === "file_write") {
    scheduleGatewayArtifactSnapshotRefresh();
    return;
  }
  if (event.event_type === "status") {
    applyGatewayStatusEvent(event);
    appendGatewayTranscriptEvent(event, "system", "status", summarizeWorkbenchStreamEvent(event));
    if (isTerminalGatewayStreamEvent(event)) {
      scheduleGatewayArtifactSnapshotRefresh();
      window.setTimeout(() => refreshDevShelfSnapshot({ silent: true }), 800);
    }
    return;
  }
  if (event.event_type === "artifact_candidate") {
    upsertLiveGatewayCandidate(event);
    appendGatewayTranscriptEvent(event, "system", "artifact_candidate", summarizeWorkbenchStreamEvent(event));
    scheduleGatewayArtifactSnapshotRefresh({
      expectCandidates: true,
      resetAttempts: true,
    });
    return;
  }
  if (event.event_type === "running_service") {
    appendGatewayTranscriptEvent(event, "assistant", "running_service", summarizeWorkbenchStreamEvent(event));
    return;
  }
  if (event.event_type === "error") {
    appendGatewayTranscriptEvent(event, "error", "error", summarizeWorkbenchStreamEvent(event));
  }
}

function applyGatewayStatusEvent(event) {
  const status = event.payload?.status;
  if (!state.devShelf.gateway.status || status !== "completed") {
    return;
  }
  state.devShelf.gateway.status = {
    ...state.devShelf.gateway.status,
    status: "completed",
    finished_at: state.devShelf.gateway.status.finished_at || event.ts,
  };
}

function ensureGatewayCandidatesPayload() {
  if (
    state.devShelf.gateway.candidates?.payload
    && typeof state.devShelf.gateway.candidates.payload === "object"
  ) {
    const payload = state.devShelf.gateway.candidates.payload;
    if (!Array.isArray(payload.preview_artifacts)) {
      payload.preview_artifacts = [];
    }
    if (!payload.summary || typeof payload.summary !== "object") {
      payload.summary = {};
    }
    return payload;
  }

  const payload = {
    schema_version: "1.0",
    run_id: state.devShelf.selectedRunId,
    gateway_session_id: state.devShelf.gateway.sessionId,
    summary: { candidate_count: 0, skipped_count: 0 },
    candidates: [],
    skipped: [],
    preview_artifacts: [],
  };
  state.devShelf.gateway.candidates = {
    run_id: state.devShelf.selectedRunId,
    gateway_session_id: state.devShelf.gateway.sessionId,
    path: null,
    payload,
  };
  return payload;
}

function upsertLiveGatewayCandidate(event) {
  const payload = event.payload || {};
  const artifactId = payload.artifact_id || payload.path || payload.candidate_id;
  if (!artifactId) {
    return;
  }

  const candidatesPayload = ensureGatewayCandidatesPayload();
  const previews = candidatesPayload.preview_artifacts;
  const candidateId = payload.candidate_id || `stream-${event.event_id || event.runtime_sequence || artifactId}`;
  const existingIndex = previews.findIndex((item) => (
    (candidateId && item.candidate_id === candidateId)
    || item.artifact_id === artifactId
  ));
  const existing = existingIndex >= 0 ? previews[existingIndex] : {};
  const preview = {
    ...existing,
    artifact_id: String(artifactId),
    title: payload.title || existing.title || labelArtifact(artifactId),
    status: payload.status || existing.status || "draft",
    path: payload.path || existing.path || null,
    produced_by: payload.produced_by || existing.produced_by,
    updated_at: event.ts || existing.updated_at || null,
    content: existing.content || null,
    content_format: existing.content_format || null,
    content_truncated: Boolean(existing.content_truncated),
    content_error: existing.content_error || "产物内容正在准备，稍后会自动刷新预览。",
    review_required: payload.review_required === undefined ? true : Boolean(payload.review_required),
    candidate_id: candidateId,
    source: "gateway_candidate",
    live_pending: true,
  };
  if (existingIndex >= 0) {
    previews[existingIndex] = preview;
  } else {
    previews.push(preview);
  }
  candidatesPayload.summary.candidate_count = Math.max(
    Number(candidatesPayload.summary.candidate_count) || 0,
    previews.length,
  );
  ensureSelectedArtifact();
}

function ensureGatewayTranscript() {
  const transcript = state.devShelf.gateway.transcript;
  if (transcript && Array.isArray(transcript.messages)) {
    return transcript;
  }
  state.devShelf.gateway.transcript = {
    message_count: 0,
    event_count: 0,
    messages: [],
  };
  return state.devShelf.gateway.transcript;
}

function mergeGatewayTranscript(current, incoming) {
  const currentMessages = Array.isArray(current?.messages) ? current.messages : [];
  const incomingMessages = Array.isArray(incoming?.messages) ? incoming.messages : [];
  const messagesByKey = new Map();

  const addMessage = (message) => {
    if (!message || typeof message !== "object") {
      return;
    }
    const key = gatewayTranscriptMergeKey(message);
    const existing = messagesByKey.get(key);
    if (!existing) {
      messagesByKey.set(key, message);
      return;
    }
    messagesByKey.set(key, preferGatewayTranscriptMessage(existing, message));
  };

  currentMessages.forEach(addMessage);
  incomingMessages.forEach(addMessage);
  const messages = [...messagesByKey.values()];
  messages.sort((left, right) => {
    const leftSequence = Number(left.sequence_start ?? left.sequence_end ?? 0);
    const rightSequence = Number(right.sequence_start ?? right.sequence_end ?? 0);
    if (leftSequence !== rightSequence) {
      return leftSequence - rightSequence;
    }
    return String(left.ts || "").localeCompare(String(right.ts || ""));
  });

  return {
    ...(current || {}),
    ...(incoming || {}),
    messages: messages.slice(-200),
    message_count: Math.min(messages.length, 200),
    event_count: Math.max(Number(current?.event_count) || 0, Number(incoming?.event_count) || 0),
  };
}

function gatewayTranscriptMergeKey(message) {
  if (message.sequence_start !== undefined && message.sequence_start !== null) {
    return [
      "seq",
      message.role || "",
      message.kind || "",
      message.sequence_start,
    ].join(":");
  }
  if (message.live_key) {
    return `live:${message.live_key}`;
  }
  return [
    "text",
    message.role || "",
    message.kind || "",
    normalizeCollabMessageText(message.text || ""),
  ].join(":");
}

function preferGatewayTranscriptMessage(current, next) {
  const currentText = String(current.text || "");
  const nextText = String(next.text || "");
  if (current.live_key && !next.live_key && !current.live_final && !next.live_final) {
    return current;
  }
  if (next.live_final && !current.live_final) {
    return current.live_key && !next.live_key ? { ...next, live_key: current.live_key } : next;
  }
  if (current.live_final && !next.live_final) {
    return current;
  }
  const preferred = nextText.length >= currentText.length ? next : current;
  if (current.live_key && !preferred.live_key) {
    return { ...preferred, live_key: current.live_key };
  }
  return preferred;
}

function appendGatewayAssistantDelta(event, delta) {
  const transcript = ensureGatewayTranscript();
  const sequence = event.runtime_sequence || event.cursor;
  const liveKey = `assistant:${sequence || "live"}`;
  let message = state.devShelf.gateway.liveAssistantMessageKey
    ? transcript.messages.find((item) => item.live_key === state.devShelf.gateway.liveAssistantMessageKey)
    : null;
  if (!message || message.role !== "assistant" || message.live_final) {
    message = {
      role: "assistant",
      kind: "message",
      text: "",
      sequence_start: sequence,
      sequence_end: sequence,
      ts: event.ts,
      live_key: liveKey,
    };
    transcript.messages.push(message);
    state.devShelf.gateway.liveAssistantMessageKey = liveKey;
  }
  message.text = `${message.text || ""}${delta}`;
  message.sequence_end = sequence || message.sequence_end;
  message.ts = message.ts || event.ts;
  trimGatewayTranscript(transcript);
}

function upsertGatewayAssistantMessage(event, text) {
  const transcript = ensureGatewayTranscript();
  let message = state.devShelf.gateway.liveAssistantMessageKey
    ? transcript.messages.find((item) => item.live_key === state.devShelf.gateway.liveAssistantMessageKey)
    : null;
  if (!message) {
    message = [...transcript.messages].reverse().find((item) => item.role === "assistant");
  }
  if (!message) {
    message = {
      role: "assistant",
      kind: "message",
      text: "",
      sequence_start: event.runtime_sequence || event.cursor,
      sequence_end: event.runtime_sequence || event.cursor,
      ts: event.ts,
    };
    transcript.messages.push(message);
  }
  message.text = text;
  message.sequence_end = event.runtime_sequence || event.cursor || message.sequence_end;
  message.ts = message.ts || event.ts;
  message.live_final = true;
  state.devShelf.gateway.liveAssistantMessageKey = null;
  trimGatewayTranscript(transcript);
}

function appendGatewayTranscriptEvent(event, role, kind, text) {
  if (!text) {
    return;
  }
  const transcript = ensureGatewayTranscript();
  const key = event.event_id || `${event.event_type}:${event.runtime_sequence || event.cursor}:${text}`;
  if (transcript.messages.some((item) => item.live_key === key)) {
    return;
  }
  transcript.messages.push({
    role,
    kind,
    text,
    sequence_start: event.runtime_sequence || event.cursor,
    sequence_end: event.runtime_sequence || event.cursor,
    ts: event.ts,
    live_key: key,
  });
  trimGatewayTranscript(transcript);
}

function trimGatewayTranscript(transcript) {
  transcript.messages = transcript.messages.slice(-200);
  transcript.message_count = transcript.messages.length;
}

async function startDevShelfGateway() {
  const runId = state.devShelf.selectedRunId;
  if (!runId) {
    return;
  }
  const control = state.devShelf.gateway.control;
  control.busy = true;
  control.message = null;
  control.error = null;
  renderDevShelf();

  try {
    const payload = {
      account: currentGatewayProvider() === "openai-codex" ? (elements.devShelfGatewayAccount.value || "a") : null,
      provider: currentGatewayProvider(),
      model: elements.devShelfGatewayModelControl.value || defaultGatewayModel(currentGatewayProvider()),
      no_session: true,
      light_mode: elements.devShelfGatewayLightMode.checked,
    };
    const response = await fetch(`/api/dev-shelf/runs/${runId}/gateway/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "启动 Gateway 失败");
    }
    control.lastResponse = result;
    control.message = result.message || "Gateway 已启动。";
    closeGatewayEventStream();
    clearGatewayArtifactSnapshotRefresh();
    state.devShelf.gateway.events = [];
    state.devShelf.gateway.transcript = null;
    state.devShelf.gateway.result = null;
    state.devShelf.gateway.candidates = null;
    state.devShelf.gateway.cursor = 0;
    await loadGatewaySnapshot(runId);
    ensureSelectedArtifact();
  } catch (error) {
    control.error = error.message;
  } finally {
    control.busy = false;
    renderDevShelf();
  }
}

async function abortDevShelfGateway() {
  const runId = state.devShelf.selectedRunId;
  if (!runId) {
    return;
  }
  const control = state.devShelf.gateway.control;
  control.busy = true;
  control.message = null;
  control.error = null;
  renderDevShelf();

  try {
    const response = await fetch(`/api/dev-shelf/runs/${runId}/gateway/abort`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "中止 Gateway 失败");
    }
    control.lastResponse = result;
    control.message = result.message || labelGatewayControlStatus(result.status);
    await loadGatewaySnapshot(runId);
    ensureSelectedArtifact();
  } catch (error) {
    control.error = error.message;
  } finally {
    control.busy = false;
    renderDevShelf();
  }
}

async function registerDevShelfGatewayResult() {
  const runId = state.devShelf.selectedRunId;
  if (!runId || !canRegisterImplementationResult(state.devShelf.detail)) {
    return;
  }
  const control = state.devShelf.gateway.control;
  control.busy = true;
  control.message = null;
  control.error = null;
  renderDevShelf();

  try {
    const response = await fetch(`/api/dev-shelf/runs/${runId}/gateway/register-result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.devShelf.gateway.sessionId,
        note: "用户在 Workbench 登记本轮执行结果。",
      }),
    });
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "登记执行结果失败");
    }
    state.devShelf.detail = result;
    control.lastResponse = null;
    control.message = "执行结果已登记，正在刷新下一步。";
    await refreshDevShelfSnapshot({ silent: false });
    ensureSelectedArtifact();
  } catch (error) {
    control.error = error.message;
  } finally {
    control.busy = false;
    renderDevShelf();
  }
}

async function selectDevShelfRun(runId) {
  state.devShelf.loading = true;
  state.devShelf.error = null;
  state.devShelf.selectedArtifactId = null;
  resetArtifactPreviewState();
  state.devShelf.flowDocumentsOpen = null;
  state.devShelf.gateway.panelOpen = null;
  closeGatewayEventStream();
  state.devShelf.createRun.visible = false;
  state.devShelf.artifactAction = defaultArtifactActionState();
  state.devShelf.collabFeedback = { message: null, error: null };
  renderDevShelf();

  try {
    await loadDevShelfRunDetail(runId);
  } catch (error) {
    state.devShelf.error = error.message;
  } finally {
    state.devShelf.loading = false;
    renderDevShelf();
  }
}

function ensureSelectedArtifact() {
  const artifacts = visibleDevShelfArtifacts();
  if (!artifacts.length) {
    state.devShelf.selectedArtifactId = null;
    resetArtifactPreviewState();
    return;
  }
  if (artifacts.some((item) => item.artifact_id === state.devShelf.selectedArtifactId)) {
    return;
  }
  state.devShelf.selectedArtifactId = defaultArtifactForDetail(artifacts)?.artifact_id || artifacts[0].artifact_id;
}

function candidatePreviewArtifacts() {
  if (!gatewayCompletedForCurrentPacket(state.devShelf.detail)) {
    return [];
  }
  const previews = state.devShelf.gateway.candidates?.payload?.preview_artifacts || [];
  if (!Array.isArray(previews)) {
    return [];
  }
  return previews
    .filter((item) => item && item.artifact_id)
    .map((item) => ({
      ...item,
      source: item.source || "gateway_candidate",
      review_required: Boolean(item.review_required),
    }));
}

function pendingCandidatePreviewArtifacts() {
  const detailArtifacts = state.devShelf.detail?.artifacts || [];
  const acceptedIds = new Set(
    detailArtifacts
      .filter((item) => item.status && !["missing", "rejected"].includes(item.status))
      .map((item) => item.artifact_id),
  );
  return candidatePreviewArtifacts().filter((item) => !acceptedIds.has(item.artifact_id));
}

function visibleDevShelfArtifacts() {
  const detailArtifacts = state.devShelf.detail?.artifacts || [];
  const previews = pendingCandidatePreviewArtifacts();
  const sortArtifacts = (artifacts) => [...artifacts].sort((left, right) => {
    const leftPriority = artifactSortPriority(left);
    const rightPriority = artifactSortPriority(right);
    if (leftPriority !== rightPriority) {
      return leftPriority - rightPriority;
    }
    return (left.title || left.artifact_id || "").localeCompare(right.title || right.artifact_id || "", "zh-CN");
  });
  if (!previews.length) {
    return sortArtifacts(detailArtifacts);
  }

  const previewIds = new Set(previews.map((item) => item.artifact_id));
  const remaining = detailArtifacts.filter((item) => !previewIds.has(item.artifact_id));
  return sortArtifacts([...previews, ...remaining]);
}

function artifactSortPriority(artifact) {
  const detail = state.devShelf.detail;
  if (artifact.source === "gateway_candidate") {
    return -10;
  }
  if (["in_review", "draft"].includes(artifact.status)) {
    return -9;
  }
  if (detail?.status === "completed") {
    const completedPriority = {
      final_summary: 0,
      review_report: 1,
      implementation_result: 2,
    };
    return completedPriority[artifact.artifact_id] ?? 10;
  }
  if (isExecutionContext(detail)) {
    const executionPriority = {
      implementation_result: 0,
      execution_todo_json: 1,
      execution_todo: 2,
    };
    return executionPriority[artifact.artifact_id] ?? 10;
  }
  const defaultPriority = {
    final_summary: 0,
    review_report: 1,
    implementation_result: 2,
    execution_todo_json: 3,
    execution_todo: 4,
  };
  return defaultPriority[artifact.artifact_id] ?? 20;
}

function defaultArtifactForDetail(artifacts) {
  if (!artifacts.length) {
    return null;
  }
  return artifacts.find((item) => item.source === "gateway_candidate")
    || artifacts.find((item) => item.status === "in_review" || item.status === "draft")
    || (state.devShelf.detail?.status === "completed"
      ? artifacts.find((item) => ["final_summary", "review_report", "implementation_result"].includes(item.artifact_id))
      : null)
    || (isExecutionContext(state.devShelf.detail)
      ? artifacts.find((item) => item.artifact_id === "implementation_result")
      : null)
    || artifacts.find((item) => item.content || item.content_error)
    || artifacts[0];
}

function artifactPreviewabilityLabel(artifact) {
  if (artifact.content || artifact.previewable) {
    return artifact.content_truncated ? "预览：摘要" : "预览：可打开";
  }
  if (artifact.content_error) {
    return "预览：受限";
  }
  return "预览：无";
}

function artifactRevisionMeta(artifact) {
  const parts = [];
  if (artifact.current_revision_id) {
    parts.push(`版本：${artifact.current_revision_id}`);
  } else if (artifact.revision_count) {
    parts.push(`版本数：${artifact.revision_count}`);
  }
  if (artifact.feedback_count) {
    parts.push(`反馈：${artifact.feedback_count}`);
  }
  return parts.join(" · ");
}

function selectedDevShelfArtifact() {
  return visibleDevShelfArtifacts()
    .find((item) => item.artifact_id === state.devShelf.selectedArtifactId) || null;
}

function renderDevShelf({ preservePageScroll = false } = {}) {
  const pageScrollSnapshot = preservePageScroll ? capturePageScrollPosition() : null;
  renderProjectCreate();
  renderDevShelfRuns();
  renderDevShelfDetail();
  renderBackendHealthStatus();
  renderAutoRefreshStatus();
  renderDirectoryPicker();
  elements.refreshRunsButton.disabled = state.devShelf.loading;
  elements.refreshRunsButton.textContent = state.devShelf.loading ? "读取中" : "刷新";
  restorePageScrollPosition(pageScrollSnapshot);
}

function renderProjectCreate() {
  const createRun = state.devShelf.createRun;
  const shouldShow = createRun.visible || !state.devShelf.selectedRunId;
  elements.projectIntakePanel.classList.toggle("hidden", !shouldShow);
  elements.newRunButton.disabled = createRun.busy;
  elements.newRunButton.textContent = shouldShow ? "填写中" : "新建";
  if (!shouldShow) {
    return;
  }

  const disabled = createRun.busy;
  elements.newRunButton.disabled = disabled;
  elements.projectNameInput.disabled = disabled;
  elements.projectTaskTypeInput.disabled = disabled;
  elements.projectContextInput.disabled = disabled;
  elements.projectPathInput.disabled = disabled;
  elements.projectPathBrowseButton.disabled = disabled;
  elements.projectRequirementInput.disabled = disabled;
  elements.projectWorkspaceConfirmedInput.disabled = disabled;
  elements.projectCreateButton.disabled = disabled;
  elements.projectCreateButton.textContent = disabled ? "创建中" : "创建 run";

  if (createRun.error) {
    elements.projectCreateBadge.textContent = "失败";
    elements.projectCreateBadge.className = "badge waiting";
    elements.projectCreateStatus.textContent = createRun.error;
    elements.projectCreateStatus.className = "project-create-status error";
    return;
  }
  if (createRun.message) {
    elements.projectCreateBadge.textContent = "已创建";
    elements.projectCreateBadge.className = "badge ready";
    elements.projectCreateStatus.textContent = createRun.message;
    elements.projectCreateStatus.className = "project-create-status";
    return;
  }
  elements.projectCreateBadge.textContent = disabled ? "创建中" : "待输入";
  elements.projectCreateBadge.className = "badge subtle";
  elements.projectCreateStatus.textContent =
    createRun.pathMessage || "创建后会自动选中新 run，并显示需求草稿等中间产物。";
  elements.projectCreateStatus.className = "project-create-status";
}

function renderDirectoryPicker() {
  const dirPicker = state.devShelf.dirPicker;
  elements.directoryPickerModal.classList.toggle("hidden", !dirPicker.open);
  if (!dirPicker.open) {
    return;
  }

  elements.directoryPickerCurrent.textContent = dirPicker.currentPath || dirPicker.rootPath || "-";
  elements.directoryPickerUpButton.disabled = dirPicker.loading || !dirPicker.parentPath;
  elements.directoryNewButton.disabled = dirPicker.loading || !dirPicker.currentPath;
  elements.directoryChooseButton.disabled = dirPicker.loading || !dirPicker.currentPath;

  elements.directoryPickerList.innerHTML = "";
  if (dirPicker.loading && !dirPicker.entries.length) {
    elements.directoryPickerList.innerHTML = '<p class="empty-state">正在读取目录。</p>';
  } else if (!dirPicker.entries.length) {
    elements.directoryPickerList.innerHTML = '<p class="empty-state">当前目录下没有子目录。</p>';
  } else {
    for (const entry of dirPicker.entries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-item";
      button.addEventListener("click", () => loadProjectDirectories(entry.path));

      const name = document.createElement("strong");
      name.textContent = entry.name;
      const path = document.createElement("span");
      path.textContent = entry.path;

      button.append(name, path);
      elements.directoryPickerList.appendChild(button);
    }
  }

  if (dirPicker.error) {
    elements.directoryPickerStatus.textContent = dirPicker.error;
    elements.directoryPickerStatus.className = "directory-picker-status error";
    return;
  }
  if (dirPicker.loading) {
    elements.directoryPickerStatus.textContent = "读取中。";
    elements.directoryPickerStatus.className = "directory-picker-status";
    return;
  }
  elements.directoryPickerStatus.textContent = dirPicker.rootPath
    ? `可选范围：${dirPicker.rootPath}`
    : "选择或新建一个项目目录。";
  elements.directoryPickerStatus.className = "directory-picker-status";
}

function renderAutoRefreshStatus() {
  if (!elements.autoRefreshStatus) {
    return;
  }
  if (state.devShelf.autoRefreshInFlight) {
    elements.autoRefreshStatus.textContent = "自动刷新中";
    return;
  }
  if (state.devShelf.autoRefreshError) {
    elements.autoRefreshStatus.textContent = "自动刷新失败，等待下一轮恢复";
    return;
  }
  if (state.devShelf.lastRefreshedAt) {
    elements.autoRefreshStatus.textContent = `上次更新 ${formatTime(state.devShelf.lastRefreshedAt)}`;
    return;
  }
  elements.autoRefreshStatus.textContent = "自动刷新每 5 秒一次";
}

function renderBackendHealthStatus() {
  const health = state.devShelf.backendHealth;
  elements.backendHealthStatus.textContent = health.message || "后端连接检测中。";
  elements.backendHealthStatus.className = `backend-health-status ${health.status || "checking"}`;
}

function renderDevShelfRuns() {
  elements.devShelfRunList.innerHTML = "";
  if (state.devShelf.error) {
    const error = document.createElement("p");
    error.className = "empty-state";
    error.textContent = state.devShelf.error;
    elements.devShelfRunList.appendChild(error);
    return;
  }
  if (state.devShelf.loading && !state.devShelf.runs.length) {
    const loading = document.createElement("p");
    loading.className = "empty-state";
    loading.textContent = "正在读取任务。";
    elements.devShelfRunList.appendChild(loading);
    return;
  }
  if (!state.devShelf.runs.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "没有找到 dev-shelf 任务。";
    elements.devShelfRunList.appendChild(empty);
    return;
  }

  for (const run of state.devShelf.runs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `run-list-item ${run.run_id === state.devShelf.selectedRunId ? "active" : ""}`;
    button.addEventListener("click", () => selectDevShelfRun(run.run_id));

    const title = document.createElement("span");
    title.className = "run-list-title";
    title.textContent = run.project_name || run.run_id;

    const meta = document.createElement("span");
    meta.className = "run-list-meta";
    meta.textContent = `${labelStage(run.current_stage)} · ${labelRunStatus(run.status)}`;

    button.append(title, meta);
    elements.devShelfRunList.appendChild(button);
  }
}

function renderDevShelfDetail() {
  const detail = state.devShelf.detail;
  if (!detail) {
    elements.devShelfRunBadge.textContent = "未选择";
    elements.devShelfRunBadge.className = "badge subtle";
    renderRunActionControls(null);
    elements.devShelfRunId.textContent = "-";
    elements.devShelfRunStage.textContent = "-";
    elements.devShelfRunStatus.textContent = "-";
    elements.devShelfPacketTarget.textContent = "-";
    renderWorkStatus(null);
    renderProgressStrip(null);
    renderExecutionWorkbench(null);
    renderDevShelfGateway(null);
    elements.devShelfPacketMeta.textContent = "-";
    elements.devShelfArtifacts.innerHTML = '<p class="empty-state">请选择一个任务。</p>';
    elements.devShelfArtifactPreviewMeta.textContent = "-";
    renderArtifactAction(null);
    elements.devShelfArtifactPreview.textContent = "请选择一个产物。";
    renderCollabPanel(null);
    renderArtifactPreviewModal([]);
    renderFlowDocuments(null, []);
    return;
  }

  elements.devShelfRunBadge.textContent = labelRunStatus(detail.status);
  elements.devShelfRunBadge.className = `badge ${runStatusClass(detail.status)}`;
  renderRunActionControls(detail);
  elements.devShelfRunId.textContent = detail.run_id;
  elements.devShelfRunStage.textContent = labelStage(detail.current_stage);
  elements.devShelfRunStatus.textContent = labelRunStatus(detail.status);
  elements.devShelfPacketTarget.textContent = buildDevShelfNextAction(detail);
  renderWorkStatus(detail);
  renderProgressStrip(detail);
  renderExecutionWorkbench(detail);
  renderDevShelfGateway(detail);
  const artifacts = visibleDevShelfArtifacts();
  renderFlowDocuments(detail, artifacts);
  renderDevShelfArtifacts(artifacts);
  renderSelectedArtifactPreview(artifacts);
  renderDevShelfPacket(detail.latest_packet);
  renderCollabPanel(detail);
  renderArtifactPreviewModal(artifacts);
}

function renderRunActionControls(detail) {
  const runAction = state.devShelf.runAction;
  const canCancel = canCancelRun(detail);
  elements.devShelfCancelRunButton.disabled = !canCancel || runAction.busy;
  elements.devShelfCancelRunButton.textContent = runAction.busy ? "终止中" : "终止任务";

  if (!detail) {
    elements.devShelfRunActionStatus.textContent = "请选择一个任务。";
    elements.devShelfRunActionStatus.className = "run-action-status";
    return;
  }
  if (runAction.error) {
    elements.devShelfRunActionStatus.textContent = runAction.error;
    elements.devShelfRunActionStatus.className = "run-action-status error";
    return;
  }
  if (runAction.message) {
    elements.devShelfRunActionStatus.textContent = runAction.message;
    elements.devShelfRunActionStatus.className = "run-action-status";
    return;
  }
  if (detail.status === "cancelled") {
    elements.devShelfRunActionStatus.textContent = "该任务已终止，不会继续推进。";
    elements.devShelfRunActionStatus.className = "run-action-status";
    return;
  }
  if (detail.status === "completed") {
    elements.devShelfRunActionStatus.textContent = "该任务已完成。";
    elements.devShelfRunActionStatus.className = "run-action-status";
    return;
  }
  elements.devShelfRunActionStatus.textContent = "当前任务可继续推进；需要停止时可终止任务。";
  elements.devShelfRunActionStatus.className = "run-action-status";
}

function renderWorkStatus(detail) {
  const status = buildWorkStatus(detail);
  elements.devShelfWorkStatusTitle.textContent = status.title;
  elements.devShelfWorkStatusMessage.textContent = status.message;
  renderPrimaryAction(detail);
}

function buildWorkStatus(detail) {
  if (!detail) {
    return {
      title: "请选择一个任务",
      message: "创建或选择 run 后，这里会显示当前是否正在处理、等待查看，还是已经完成。",
    };
  }

  const gateway = state.devShelf.gateway;
  const control = gateway.control || defaultGatewayControlState();
  const previews = pendingCandidatePreviewArtifacts();
  const execution = isExecutionContext(detail);
  if (control.busy) {
    return {
      title: "正在发送操作",
      message: "页面已经收到你的操作，正在请求后台处理；自动刷新会继续拉取最新状态。",
    };
  }
  if (isWaitingForStartedGatewaySession()) {
    return {
      title: execution ? "代码实现已启动" : "pi-agent 已启动",
      message: execution
        ? "正在等待第一批运行状态；页面会自动刷新执行对话和结果摘要。"
        : "正在等待第一批运行状态；页面会自动刷新，出现产物后会在下方预览区展示。",
    };
  }
  if (gateway.status?.status === "starting") {
    return {
      title: execution ? "pi-agent 正在实现" : "pi-agent 正在处理",
      message: `已收到 ${gateway.status.event_count || 0} 条运行事件；右侧协作对话会显示筛选后的实时进展。`,
    };
  }
  if (gateway.status?.status === "failed") {
    return {
      title: "pi-agent 执行失败",
      message: latestGatewayErrorText() || "请到页面底部的执行详情查看错误信息。",
    };
  }
  if (gateway.status?.abort_requested) {
    return {
      title: "正在中止 pi-agent",
      message: "后台已收到中止请求，等待运行进程退出并刷新最终状态。",
    };
  }
  if (canRegisterImplementationResult(detail)) {
    return {
      title: "执行结果待登记",
      message: "pi-agent 已完成并写入项目文件；先登记执行结果，再让流程进入下一阶段。",
    };
  }
  if (previews.length) {
    return {
      title: "已生成中间产物，等待查看",
      message: `${buildPendingArtifactReviewAction(previews)}。确认前不会继续生成下一份产物。`,
    };
  }
  if (gatewayCompletedForCurrentPacket(detail) && !gatewayCompletedWithoutProducedOutputs(detail)) {
    const summary = gatewayResultSummary();
    const producedCount = summary?.produced_count ?? 0;
    return {
      title: execution ? "上次代码执行已完成" : "pi-agent 已结束",
      message: producedCount > 0
        ? "本次执行已经登记结果；等待流程刷新出下一步后再继续，不重复启动同一份 packet。"
        : "本次执行已完成，但实现结果未登记；请查看 pi-agent 对话、最近写入文件和结果摘要。",
    };
  }
  if (detail.status === "awaiting_human") {
    return {
      title: "等待人工确认",
      message: "当前流程停在人工确认点；先查看中间产物，再决定是否继续推进。",
    };
  }
  if (detail.status === "cancelled") {
    return {
      title: "任务已终止",
      message: "这个 run 不会继续推进；执行详情和已有产物仍可查看。",
    };
  }
  if (detail.status === "completed") {
    return {
      title: "任务已完成",
      message: "当前 run 已结束，可以查看最终产物和执行详情。",
    };
  }
  const primaryAction = primaryActionForDetail(detail);
  if (primaryAction?.type === "workflow_continue") {
    return {
      title: "等待进入下一阶段",
      message: `${buildDevShelfNextAction(detail)}。点击继续流程只写入阶段事件，不启动 pi-agent。`,
    };
  }
  if (primaryAction?.type === "gateway_start") {
    const retryMissingOutput = gatewayCompletedWithoutProducedOutputs(detail);
    return {
      title: retryMissingOutput ? "生成未产出" : execution ? "可以开始代码实现" : "可以生成下一份中间产物",
      message: retryMissingOutput
        ? buildGatewayEmptyOutputAdvice(detail)
        : execution
        ? `${buildDevShelfNextAction(detail)}。点击开始代码实现，让 pi-agent 按 execution_todo 修改真实项目。`
        : `${buildDevShelfNextAction(detail)}。点击启动生成，让 pi-agent 继续产出下一份可预览内容。`,
    };
  }
  if (gateway.status?.status === "completed") {
    return {
      title: "pi-agent 已结束",
      message: "本次执行已完成，但没有发现新的可预览产物；可查看底部执行详情排查。",
    };
  }
  const nextAction = buildDevShelfNextAction(detail);
  return {
    title: "等待下一步执行",
    message: nextAction,
  };
}

function renderPrimaryAction(detail) {
  const control = state.devShelf.gateway.control || defaultGatewayControlState();
  const runAction = state.devShelf.runAction;
  const action = primaryActionForDetail(detail);
  const visible = Boolean(action);
  const busy = Boolean(control.busy || runAction.busy);
  elements.devShelfPrimaryActions.classList.toggle("hidden", !visible);
  elements.devShelfPrimaryStartButton.disabled = !visible || busy;
  elements.devShelfPrimaryStartButton.textContent = busy ? "处理中" : action?.label || "继续";
}

function shouldShowPrimaryStart(detail) {
  return Boolean(primaryActionForDetail(detail));
}

function primaryActionForDetail(detail) {
  if (!detail || isTerminalRunStatus(detail.status)) {
    return null;
  }
  if (!["in_progress", "ready_for_next_stage"].includes(detail.status)) {
    return null;
  }
  if ((detail.pending_human_gates || []).length) {
    return null;
  }
  if (pendingCandidatePreviewArtifacts().length) {
    return null;
  }
  const gatewayStatus = state.devShelf.gateway.status?.status;
  if (
    gatewayStatus === "starting"
    || state.devShelf.gateway.status?.abort_requested
    || isWaitingForStartedGatewaySession()
  ) {
    return null;
  }

  const decision = packetDecision(detail);
  if (decision === "enter_stage") {
    const target = packetTarget(detail);
    return {
      type: "workflow_continue",
      label: target === "stage.execution" ? "进入执行阶段" : "继续流程",
    };
  }
  if (canStartGatewayForRun(detail)) {
    const policyAction = actionPolicy(detail, "can_start_gateway");
    return { type: "gateway_start", label: policyAction?.label || (isExecutionContext(detail) ? "开始代码实现" : "启动生成") };
  }
  if (canRegisterImplementationResult(detail)) {
    const policyAction = actionPolicy(detail, "can_register_implementation_result");
    return { type: "register_result", label: policyAction?.label || "登记执行结果" };
  }
  return null;
}

function actionPolicy(detail, key) {
  return detail?.action_policy?.actions?.[key] || null;
}

function packetContent(detail) {
  return detail?.latest_packet?.content || {};
}

function packetDecision(detail) {
  return detail?.router?.decision_type || detail?.latest_packet?.decision_type || null;
}

function packetTarget(detail) {
  return detail?.router?.target || detail?.latest_packet?.target || null;
}

function packetPendingOutputs(detail) {
  const content = packetContent(detail);
  const outputs = Array.isArray(content.pending_outputs)
    ? content.pending_outputs
    : content.outputs_to_produce;
  return Array.isArray(outputs)
    ? outputs.filter((item) => item && typeof item === "object")
    : [];
}

function packetWorkspacePath(detail) {
  const packet = packetContent(detail);
  const workspace = packet.workspace || {};
  const runtime = packet.agent_runtime_contract || {};
  return workspace.project_path || runtime.cwd || null;
}

function canStartGatewayForRun(detail) {
  const policyAction = actionPolicy(detail, "can_start_gateway");
  if (policyAction) {
    return Boolean(policyAction.allowed);
  }
  if (!detail || isTerminalRunStatus(detail.status)) {
    return false;
  }
  if (packetDecision(detail) !== "run_manifest") {
    return false;
  }
  if (!packetPendingOutputs(detail).length) {
    return false;
  }
  if (canRegisterImplementationResult(detail)) {
    return false;
  }
  if (
    gatewayCompletedForCurrentPacket(detail)
    && (isExecutionContext(detail) || !gatewayCompletedWithoutProducedOutputs(detail))
  ) {
    return false;
  }
  return Boolean(packetWorkspacePath(detail));
}

function canRegisterImplementationResult(detail) {
  const policyAction = actionPolicy(detail, "can_register_implementation_result");
  if (policyAction) {
    return Boolean(policyAction.allowed);
  }
  if (!detail || isTerminalRunStatus(detail.status)) {
    return false;
  }
  if (detail.current_stage !== "execution" || packetTarget(detail) !== "stage.execution") {
    return false;
  }
  const status = state.devShelf.gateway.status;
  if (status?.status !== "completed") {
    return false;
  }
  if (status.packet_target !== "stage.execution" || status.packet_path !== detail.latest_packet?.path) {
    return false;
  }
  const implementation = implementationResultArtifact(detail);
  const quickDeploy = quickDeployGuideArtifact(detail);
  const implementationDone = Boolean(implementation && implementation.status && implementation.status !== "missing");
  const quickDeployDone = Boolean(quickDeploy && quickDeploy.status && quickDeploy.status !== "missing");
  if (implementationDone && quickDeployDone) {
    return false;
  }
  return Boolean(status.gateway_session_id);
}

function gatewayResultSummary() {
  return state.devShelf.gateway.result?.payload?.summary
    || state.devShelf.gateway.status?.artifact_result_summary
    || null;
}

function gatewayProducedOutputCount() {
  const summary = gatewayResultSummary();
  return typeof summary?.produced_count === "number" ? summary.produced_count : null;
}

function gatewayMissingOutputCount() {
  const summary = gatewayResultSummary();
  return typeof summary?.missing_count === "number" ? summary.missing_count : null;
}

function gatewayCompletedForCurrentPacket(detail) {
  const status = state.devShelf.gateway.status;
  if (!detail || status?.status !== "completed") {
    return false;
  }

  const currentPacketPath = detail.latest_packet?.path;
  if (currentPacketPath && status.packet_path) {
    return currentPacketPath === status.packet_path;
  }

  const summary = gatewayResultSummary();
  return Boolean(summary && typeof summary.output_count === "number" && summary.output_count > 0);
}

function gatewayCompletedWithoutProducedOutputs(detail) {
  return gatewayCompletedForCurrentPacket(detail) && gatewayProducedOutputCount() === 0;
}

function latestGatewayErrorText() {
  const statusError = state.devShelf.gateway.status?.error;
  if (statusError) {
    return trimText(statusError, 220);
  }
  const messages = state.devShelf.gateway.transcript?.messages || [];
  const errorMessage = [...messages].reverse().find((message) => message.role === "error" && message.text);
  if (errorMessage) {
    return trimText(errorMessage.text, 220);
  }
  const errorEvent = [...(state.devShelf.gateway.events || [])]
    .reverse()
    .find((event) => event.event_type === "error" && event.payload?.message);
  return errorEvent ? trimText(errorEvent.payload.message, 220) : "";
}

function buildGatewayEmptyOutputAdvice(detail) {
  const missingCount = gatewayMissingOutputCount();
  const missingText = missingCount && missingCount > 0
    ? `缺少 ${missingCount} 个目标产物`
    : "没有写出目标产物";
  const errorText = latestGatewayErrorText();
  const errorPart = errorText ? `最近错误：${errorText}。` : "";
  return `${buildDevShelfNextAction(detail)}。上次 pi-agent ${missingText}。${errorPart}可以点击重新生成。`;
}

function isWaitingForStartedGatewaySession() {
  const gateway = state.devShelf.gateway;
  if (gateway.control?.lastResponse?.status !== "started") {
    return false;
  }
  if (!gateway.status) {
    return true;
  }
  if (gateway.status.status === "starting") {
    return true;
  }
  const launchStartedAt = Date.parse(gateway.control.lastResponse.started_at || "");
  const sessionStartedAt = Date.parse(gateway.status.started_at || "");
  if (!Number.isFinite(launchStartedAt) || !Number.isFinite(sessionStartedAt)) {
    return gateway.status.status !== "completed";
  }
  return sessionStartedAt < launchStartedAt - 1000;
}

function renderProgressStrip(detail) {
  elements.devShelfProgressStrip.innerHTML = "";
  const states = progressStates(detail);
  states.forEach((step, index) => {
    const item = document.createElement("div");
    item.className = `progress-step ${step.state}`;

    const marker = document.createElement("span");
    marker.className = "progress-marker";
    marker.textContent = step.state.includes("done") || step.state.includes("complete")
      ? "✓"
      : step.state.includes("blocked")
        ? "!"
        : String(index + 1);

    const copy = document.createElement("span");
    copy.className = "progress-copy";

    const label = document.createElement("strong");
    label.textContent = step.label;

    const detailText = document.createElement("span");
    detailText.textContent = step.detail;

    copy.append(label, detailText);
    item.append(marker, copy);
    elements.devShelfProgressStrip.appendChild(item);
  });
}

function progressStates(detail) {
  const steps = [
    { key: "intake", label: "需求输入", detail: "创建 run" },
    { key: "artifacts", label: "流程产物", detail: "产出文档" },
    { key: "human", label: "人工确认", detail: "确认边界" },
    { key: "implementation", label: "代码实现", detail: "真实改动" },
    { key: "review", label: "复查", detail: "检查结果" },
    { key: "complete", label: "完成", detail: "结束任务" },
  ];
  if (!detail) {
    return steps.map((step) => ({ ...step, state: "pending" }));
  }
  if (detail.status === "cancelled") {
    return steps.map((step, index) => ({
      ...step,
      state: index === 0 ? "done" : "blocked",
      detail: index === 0 ? step.detail : "已终止",
    }));
  }
  if (detail.status === "completed") {
    return steps.map((step, index) => ({
      ...step,
      state: index === steps.length - 1 ? "current complete" : "done",
      detail: index === steps.length - 1 ? "任务已完成" : step.detail,
    }));
  }

  const gatewayStatus = state.devShelf.gateway.status?.status;
  const hasPreview = pendingCandidatePreviewArtifacts().length > 0;
  const hasPendingGate = (detail.pending_human_gates || []).length > 0 || detail.status === "awaiting_human";
  let current = "intake";
  if (gatewayStatus === "failed") {
    current = "implementation_failed";
  } else if (hasPendingGate) {
    current = "human";
  } else if (canRegisterImplementationResult(detail)) {
    current = "implementation";
  } else if (hasPreview) {
    current = "human";
  } else if (state.devShelf.gateway.control?.lastResponse?.status === "started" || gatewayStatus === "starting") {
    current = "implementation";
  } else if (detail.current_stage === "execution") {
    current = packetTarget(detail) === "stage.review" ? "review" : "implementation";
  } else if (detail.current_stage === "review" || packetTarget(detail) === "stage.review") {
    current = "review";
  } else if (
    [
      "requirement_confirmation",
      "confirmed_requirement",
      "skill_selection",
      "spec_drafting",
      "spec_confirmation",
      "reuse_decision",
      "implementation_planning",
    ].includes(detail.current_stage)
  ) {
    current = "artifacts";
  } else {
    current = "intake";
  }

  return steps.map((step) => {
    if (current === "implementation_failed") {
      if (["intake", "artifacts", "human"].includes(step.key)) {
        return { ...step, state: "done" };
      }
      if (step.key === "implementation") {
        return { ...step, state: "blocked", detail: "执行失败" };
      }
      return { ...step, state: "pending" };
    }
    if (step.key === current) {
      return { ...step, state: "current" };
    }
    if (steps.findIndex((item) => item.key === step.key) < steps.findIndex((item) => item.key === current)) {
      return { ...step, state: "done" };
    }
    return { ...step, state: "pending" };
  });
}

function renderFlowDocuments(detail, artifacts) {
  elements.devShelfFlowDocumentsCount.textContent = detail ? `${artifacts.length} 份` : "-";
}

function renderExecutionWorkbench(detail) {
  const visible = isExecutionContext(detail);
  elements.devShelfExecutionWorkbench.classList.toggle("hidden", !visible);
  if (!visible) {
    elements.devShelfExecutionMeta.textContent = "-";
    elements.devShelfExecutionBadge.textContent = "-";
    elements.devShelfExecutionBadge.className = "badge subtle mini-badge";
    elements.devShelfExecutionWorkspace.textContent = "-";
    elements.devShelfExecutionPacket.textContent = "-";
    elements.devShelfExecutionResultStatus.textContent = "-";
    elements.devShelfExecutionLastOutput.textContent = "-";
    elements.devShelfExecutionRegisterButton.classList.add("hidden");
    elements.devShelfExecutionRegisterButton.disabled = true;
    elements.devShelfExecutionResult.textContent = "请选择一个 run。";
    return;
  }

  const overview = buildExecutionOverview(detail);
  const control = state.devShelf.gateway.control || defaultGatewayControlState();
  const canRegister = canRegisterImplementationResult(detail);
  elements.devShelfExecutionMeta.textContent = overview.message;
  elements.devShelfExecutionBadge.textContent = overview.badge;
  elements.devShelfExecutionBadge.className = `badge ${overview.className} mini-badge`;
  elements.devShelfExecutionWorkspace.textContent = packetWorkspacePath(detail) || "-";
  elements.devShelfExecutionPacket.textContent = executionPacketLabel(detail);
  elements.devShelfExecutionResultStatus.textContent = executionResultStatusText(detail);
  elements.devShelfExecutionLastOutput.textContent = latestTranscriptText() || "-";
  elements.devShelfExecutionRegisterButton.classList.toggle("hidden", !canRegister);
  elements.devShelfExecutionRegisterButton.disabled = !canRegister || control.busy;
  elements.devShelfExecutionRegisterButton.textContent = control.busy ? "登记中" : "登记执行结果";
  elements.devShelfExecutionResult.innerHTML = renderMarkdown(buildExecutionResultSummary(detail));
}

function buildExecutionOverview(detail) {
  const gatewayStatus = state.devShelf.gateway.status?.status;
  const summary = gatewayResultSummary();
  const producedCount = summary?.produced_count ?? 0;
  const implementation = implementationResultArtifact(detail);

  if (canRegisterImplementationResult(detail)) {
    return {
      badge: "待登记",
      className: "waiting",
      message: "pi-agent 已完成并写入项目文件；点击登记后，流程才会进入下一阶段。",
    };
  }
  if (gatewayStatus === "failed") {
    return {
      badge: "执行失败",
      className: "waiting",
      message: state.devShelf.gateway.status?.error || "pi-agent 本轮执行失败。",
    };
  }
  if (gatewayStatus === "starting" || isWaitingForStartedGatewaySession()) {
    return {
      badge: "执行中",
      className: "subtle",
      message: `pi-agent 正在处理，已收到 ${state.devShelf.gateway.status?.event_count || 0} 条事件。`,
    };
  }
  if (gatewayStatus === "completed") {
    if (implementation || producedCount > 0) {
      return {
        badge: "已登记",
        className: "ready",
        message: "pi-agent 已完成，且执行结果已经登记到流程产物。",
      };
    }
    return {
      badge: "待登记",
      className: "waiting",
      message: "pi-agent 已完成，但执行结果还没有登记为流程产物。",
    };
  }
  if (canStartGatewayForRun(detail)) {
    return {
      badge: "可执行",
      className: "ready",
      message: "当前 execution packet 可启动 pi-agent 修改真实项目。",
    };
  }
  return {
    badge: "待推进",
    className: "subtle",
    message: buildDevShelfNextAction(detail),
  };
}

function buildExecutionResultSummary(detail) {
  const implementation = implementationResultArtifact(detail);
  if (implementation?.content) {
    return implementation.content;
  }

  const status = state.devShelf.gateway.status;
  const summary = gatewayResultSummary();
  const writtenFiles = writtenFilesFromTranscript();
  const lines = [
    "## 实现状态",
    "",
    `- 阶段：${labelStage(detail.current_stage)}`,
    `- Gateway：${labelGatewayStatus(status?.status)}`,
    `- 产物登记：${summaryCounts(summary)}`,
    `- 项目路径：\`${packetWorkspacePath(detail) || "-"}\``,
  ];

  if (status?.packet_path) {
    lines.push(`- 执行 packet：\`${status.packet_path}\``);
  } else if (detail.latest_packet?.path) {
    lines.push(`- 执行 packet：\`${detail.latest_packet.path}\``);
  }

  if (canRegisterImplementationResult(detail)) {
    lines.push(
      "",
      "## 当前阻塞",
      "",
      "- pi-agent 已结束，但实现结果或快速部署文档尚未登记。",
      "- 点击“登记执行结果”后，Workbench 会生成实现结果文档、快速部署文档并刷新下一步。",
    );
  } else if (status?.status === "completed" && (summary?.produced_count ?? 0) === 0) {
    lines.push("", "## 当前阻塞", "", "- pi-agent 已结束，但没有发现可登记的实现结果。");
  }

  if (writtenFiles.length) {
    lines.push("", "## 最近写入文件", "");
    for (const path of writtenFiles) {
      lines.push(`- \`${path}\``);
    }
  }

  const latest = latestTranscriptText({ preferAssistant: true });
  if (latest) {
    lines.push("", "## 最近回复", "", latest);
  }

  return lines.join("\n");
}

function isExecutionContext(detail) {
  if (!detail) {
    return false;
  }
  const content = packetContent(detail);
  return detail.current_stage === "execution"
    || packetTarget(detail) === "stage.execution"
    || content.stage === "execution"
    || state.devShelf.gateway.status?.packet_target === "stage.execution";
}

function executionPacketLabel(detail) {
  const sequence = detail?.latest_packet?.sequence;
  const target = packetTarget(detail);
  return [
    sequence ? `第 ${sequence} 轮` : null,
    target ? formatTarget(target) : null,
  ].filter(Boolean).join(" · ") || "-";
}

function executionResultStatusText(detail) {
  const implementation = implementationResultArtifact(detail);
  if (implementation) {
    return `${labelArtifactStatus(implementation.status)} · ${implementation.path || "未记录路径"}`;
  }
  if (canRegisterImplementationResult(detail)) {
    return "待登记";
  }
  const summary = gatewayResultSummary();
  if (summary && typeof summary.produced_count === "number") {
    return summary.produced_count > 0 ? `已产出 ${summary.produced_count}` : "未登记";
  }
  return canStartGatewayForRun(detail) ? "待执行" : "-";
}

function implementationResultArtifact(detail) {
  return (detail?.artifacts || []).find((item) => item.artifact_id === "implementation_result") || null;
}

function quickDeployGuideArtifact(detail) {
  return (detail?.artifacts || []).find((item) => item.artifact_id === "quick_deploy_guide") || null;
}

function latestTranscriptText({ preferAssistant = false } = {}) {
  const messages = state.devShelf.gateway.transcript?.messages || [];
  const roles = preferAssistant ? ["assistant", "tool", "system", "error"] : ["tool", "assistant", "system", "error"];
  for (const role of roles) {
    const message = [...messages].reverse().find((item) => item.role === role && item.text);
    if (message) {
      return trimText(message.text, 180);
    }
  }
  return "";
}

function writtenFilesFromTranscript() {
  const messages = state.devShelf.gateway.transcript?.messages || [];
  const paths = [];
  const seen = new Set();
  const addPath = (path) => {
    if (path && !seen.has(path)) {
      seen.add(path);
      paths.push(path);
    }
  };
  for (const message of messages) {
    const text = message.text || "";
    for (const match of text.matchAll(/Successfully wrote \d+ bytes to ([^\s]+)/g)) {
      addPath(match[1]);
    }
  }
  for (const event of state.devShelf.gateway.events || []) {
    if (event.event_type === "file_write") {
      addPath(event.payload?.path);
    }
  }
  return paths.slice(-8);
}

function trimText(value, limit) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
}

function renderDevShelfGateway(detail) {
  const gateway = state.devShelf.gateway;
  const status = detail ? gateway.status : null;
  if (!status) {
    elements.devShelfGatewayStatus.textContent = gateway.error || "暂无";
    elements.devShelfGatewayStatus.className = "badge subtle mini-badge";
    elements.devShelfGatewaySummaryLine.textContent = gateway.error || "暂无执行记录。";
    elements.devShelfGatewaySession.textContent = "-";
    elements.devShelfGatewayModel.textContent = "-";
    elements.devShelfGatewayEventCount.textContent = "-";
    elements.devShelfGatewayCandidateCount.textContent = "-";
    renderGatewayTranscript([], gateway.error || "暂无 Gateway session。");
    elements.devShelfGatewayEvents.innerHTML = `<p class="empty-state">${gateway.error || "暂无 Gateway session。"}</p>`;
    elements.devShelfGatewaySummary.innerHTML = '<p class="empty-state">暂无 Gateway 结果。</p>';
    renderGatewayViewPanels();
    renderGatewayControls(detail);
    renderGatewayPanelShell(detail);
    return;
  }

  elements.devShelfGatewayStatus.textContent = labelGatewayStatus(status.status);
  elements.devShelfGatewayStatus.className = `badge ${gatewayStatusClass(status.status)} mini-badge`;
  elements.devShelfGatewaySummaryLine.textContent = buildGatewaySummaryLine(detail, status);
  elements.devShelfGatewaySession.textContent = status.gateway_session_id || "-";
  elements.devShelfGatewayModel.textContent =
    [status.provider, status.model].filter(Boolean).join(" / ") || "-";
  elements.devShelfGatewayEventCount.textContent = String(status.event_count ?? "-");
  const candidateCount = status.event_candidate_summary?.candidate_count ?? 0;
  elements.devShelfGatewayCandidateCount.textContent = String(candidateCount);
  renderGatewayTranscript(gateway.transcript?.messages || []);
  renderGatewayEvents(gateway.events || []);
  renderGatewaySummary(gateway);
  renderGatewayViewPanels();
  renderGatewayControls(detail);
  renderGatewayPanelShell(detail);
}

function renderGatewayPanelShell(detail) {
  const gateway = state.devShelf.gateway;
  const shouldOpen = gateway.panelOpen === null
    ? shouldOpenGatewayPanel(detail)
    : gateway.panelOpen;
  gateway.renderingPanel = true;
  elements.devShelfGatewayPanel.open = shouldOpen;
  gateway.renderingPanel = false;
}

function shouldOpenGatewayPanel(detail) {
  const gateway = state.devShelf.gateway;
  const status = gateway.status;
  if (!detail) {
    return false;
  }
  if (gateway.control?.busy || gateway.control?.error) {
    return true;
  }
  return Boolean(
    isWaitingForStartedGatewaySession()
    || status?.status === "starting"
    || status?.status === "failed"
    || status?.abort_requested
  );
}

function buildGatewaySummaryLine(detail, status) {
  if (!detail || !status) {
    return "暂无执行记录。";
  }
  const eventText = `${status.event_count || 0} 事件`;
  const refreshText = gatewayRefreshModeText();
  const writtenCount = writtenFilesFromTranscript().length;
  const writtenText = writtenCount ? `写入 ${writtenCount} 个文件` : "未识别写入文件";
  if (status.status === "failed") {
    return `失败 · ${eventText} · 查看错误详情`;
  }
  if (status.abort_requested) {
    return `正在中止 · ${eventText}`;
  }
  if (status.status === "starting" || isWaitingForStartedGatewaySession()) {
    return `运行中 · ${eventText} · ${refreshText}`;
  }
  if (status.status === "completed") {
    if (gatewayCompletedWithoutProducedOutputs(detail)) {
      const missingCount = gatewayMissingOutputCount();
      const missingText = missingCount && missingCount > 0 ? `缺少 ${missingCount} 个产物` : "未产出目标产物";
      return `生成未产出 · ${eventText} · ${missingText} · 可重新生成`;
    }
    const resultText = canRegisterImplementationResult(detail)
      ? "执行结果待登记"
      : implementationResultArtifact(detail)
        ? "实现结果已登记"
        : "已完成";
    return `${resultText} · ${eventText} · ${writtenText}`;
  }
  return `${labelGatewayStatus(status.status)} · ${eventText}`;
}

function gatewayRefreshModeText() {
  const gateway = state.devShelf.gateway;
  if (gateway.streamConnected) {
    return "实时同步中";
  }
  if (gateway.streamError) {
    return "轮询刷新中";
  }
  return "页面会自动刷新";
}

function renderGatewayViewPanels() {
  const activeView = state.devShelf.gateway.activeView || "chat";
  const panels = {
    chat: elements.devShelfGatewayChatPanel,
    events: elements.devShelfGatewayEventsPanel,
    summary: elements.devShelfGatewaySummaryPanel,
  };

  elements.devShelfGatewayViewButtons.forEach((button) => {
    const isActive = button.dataset.gatewayView === activeView;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });

  Object.entries(panels).forEach(([view, panel]) => {
    panel.classList.toggle("hidden", view !== activeView);
  });
}

function renderGatewayControls(detail) {
  const control = state.devShelf.gateway.control || defaultGatewayControlState();
  const canRegister = canRegisterImplementationResult(detail);
  const canStartGateway = canStartGatewayForRun(detail) && !isWaitingForStartedGatewaySession();
  const execution = isExecutionContext(detail);
  const disabled = !detail || control.busy;
  elements.devShelfGatewayAccount.disabled = disabled;
  elements.devShelfGatewayProviderControl.disabled = disabled;
  elements.devShelfGatewayModelControl.disabled = disabled;
  elements.devShelfGatewayRefreshModelsButton.disabled = disabled;
  elements.devShelfGatewayLightMode.disabled = disabled;
  elements.devShelfGatewayStartButton.disabled = disabled || !canStartGateway;
  elements.devShelfGatewayAbortButton.disabled = disabled;
  elements.devShelfGatewayStartButton.textContent = control.busy
    ? "处理中"
    : gatewayCompletedWithoutProducedOutputs(detail)
      ? "重新生成"
      : execution
        ? "开始实现"
        : "启动";
  elements.devShelfGatewayAbortButton.textContent = control.busy ? "处理中" : "中止";

  if (!detail) {
    elements.devShelfGatewayControlStatus.textContent = "请选择一个 run。";
    elements.devShelfGatewayControlStatus.className = "gateway-control-status";
    return;
  }
  if (control.error) {
    elements.devShelfGatewayControlStatus.textContent = control.error;
    elements.devShelfGatewayControlStatus.className = "gateway-control-status error";
    return;
  }
  if (control.message) {
    const last = control.lastResponse?.status ? ` · ${labelGatewayControlStatus(control.lastResponse.status)}` : "";
    elements.devShelfGatewayControlStatus.textContent = `${control.message}${last}`;
    elements.devShelfGatewayControlStatus.className = "gateway-control-status";
    return;
  }
  if (isTerminalRunStatus(detail.status)) {
    elements.devShelfGatewayControlStatus.textContent = "当前 run 已结束，不能启动 pi-agent。";
    elements.devShelfGatewayControlStatus.className = "gateway-control-status";
    return;
  }
  if (isWaitingForStartedGatewaySession()) {
    elements.devShelfGatewayControlStatus.textContent = "pi-agent 已启动，等待运行状态刷新。";
    elements.devShelfGatewayControlStatus.className = "gateway-control-status";
    return;
  }
  if (canRegister) {
    elements.devShelfGatewayControlStatus.textContent = "本轮实现已完成；先登记执行结果，不要重复启动同一轮 pi-agent。";
    elements.devShelfGatewayControlStatus.className = "gateway-control-status";
    return;
  }
  if (!canStartGateway) {
    const decision = packetDecision(detail);
    if (decision === "enter_stage") {
      elements.devShelfGatewayControlStatus.textContent = "当前需要先继续流程，不启动 pi-agent。";
    } else if (gatewayCompletedForCurrentPacket(detail)) {
      elements.devShelfGatewayControlStatus.textContent = "这份 packet 已完成执行；请查看对话/摘要，或等待流程生成下一份 packet。";
    } else if (decision !== "run_manifest" || !packetPendingOutputs(detail).length) {
      elements.devShelfGatewayControlStatus.textContent = "当前 packet 没有待生成产物，不能启动 pi-agent。";
    } else {
      elements.devShelfGatewayControlStatus.textContent = "当前 run 没有项目路径，不能启动 pi-agent。";
    }
    elements.devShelfGatewayControlStatus.className = "gateway-control-status error";
    return;
  }
  if (gatewayCompletedWithoutProducedOutputs(detail)) {
    elements.devShelfGatewayControlStatus.textContent = buildGatewayEmptyOutputAdvice(detail);
  } else {
    elements.devShelfGatewayControlStatus.textContent = execution
      ? "使用当前 execution packet 启动 pi-agent 修改项目代码。"
      : "使用当前 run 的最新 execution packet 启动 pi-agent。";
  }
  elements.devShelfGatewayControlStatus.className = "gateway-control-status";
}

function renderGatewayTranscript(messages, emptyText = "当前 Gateway session 还没有可展示对话。") {
  elements.devShelfGatewayChat.innerHTML = "";
  if (!messages.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = emptyText;
    elements.devShelfGatewayChat.appendChild(empty);
    return;
  }

  for (const message of messages) {
    const item = document.createElement("article");
    item.className = `gateway-chat-message gateway-chat-message-${message.role || "system"}`;

    const head = document.createElement("div");
    head.className = "gateway-chat-head";

    const title = document.createElement("strong");
    title.textContent = labelGatewayTranscriptMessage(message);

    const meta = document.createElement("span");
    meta.className = "minor-meta";
    const sequenceText = message.sequence_start
      ? `#${message.sequence_start}${message.sequence_end && message.sequence_end !== message.sequence_start ? `-${message.sequence_end}` : ""}`
      : "";
    meta.textContent = [sequenceText, formatTime(message.ts)].filter((item) => item && item !== "-").join(" · ");

    const body = document.createElement("div");
    body.className = "gateway-chat-body";
    if (message.role === "assistant") {
      body.classList.add("markdown-body");
      body.innerHTML = renderMarkdown(message.text || "");
    } else {
      body.textContent = message.text || "";
    }

    head.append(title, meta);
    item.append(head, body);
    elements.devShelfGatewayChat.appendChild(item);
  }
}

function renderCollabPanel(detail) {
  const gateway = state.devShelf.gateway;
  const status = gateway.status?.status;
  elements.devShelfCollabStatus.textContent = detail
    ? (status ? labelGatewayStatus(status) : labelRunStatus(detail.status))
    : "未选择";
  elements.devShelfCollabStatus.className = `badge ${status ? gatewayStatusClass(status) : "subtle"} mini-badge`;

  elements.devShelfCollabInput.disabled = !detail;
  elements.devShelfCollabSubmitButton.disabled = !detail;
  elements.devShelfCollabAbortButton.disabled = !detail || !canAbortGateway();
  renderCollabFeedbackStatus(detail);
  renderCollabMessages(detail);
}

function canAbortGateway() {
  const gateway = state.devShelf.gateway;
  if (gateway.status?.abort_requested) {
    return false;
  }
  return Boolean(
    isWaitingForStartedGatewaySession()
    || gateway.status?.status === "starting"
  );
}

function renderCollabFeedbackStatus(detail) {
  const feedback = state.devShelf.collabFeedback;
  if (!detail) {
    elements.devShelfCollabStatusText.textContent = "请选择一个 run。";
    elements.devShelfCollabStatusText.className = "artifact-action-status";
    return;
  }
  if (feedback.error) {
    elements.devShelfCollabStatusText.textContent = feedback.error;
    elements.devShelfCollabStatusText.className = "artifact-action-status error";
    return;
  }
  if (feedback.message) {
    elements.devShelfCollabStatusText.textContent = feedback.message;
    elements.devShelfCollabStatusText.className = "artifact-action-status";
    return;
  }
  if (state.devShelf.gateway.status?.abort_requested) {
    elements.devShelfCollabStatusText.textContent = "正在中止 pi-agent；停止后可继续提交修改意见并重新生成。";
    elements.devShelfCollabStatusText.className = "artifact-action-status";
    return;
  }
  elements.devShelfCollabStatusText.textContent = canAbortGateway()
    ? "运行中可先中止 pi-agent，再补充修改意见。"
    : "选择待确认产物后，可提交修改意见并重新生成当前产物。";
  elements.devShelfCollabStatusText.className = "artifact-action-status";
}

function renderCollabMessages(detail) {
  syncCollabScrollContext(detail);
  const scrollSnapshot = captureScrollablePosition(elements.devShelfCollabChat);
  const shouldStickToBottom = !state.devShelf.collabScroll.userDetached && scrollSnapshot.nearBottom;
  if (!detail) {
    renderCollabEmptyState("请选择一个 run。");
    state.devShelf.collabScroll.userDetached = false;
    state.devShelf.collabScroll.hasNewMessages = false;
    state.devShelf.collabScroll.messageSignature = null;
    renderCollabNewMessageNotice();
    return;
  }

  const messages = buildCollabMessages(detail);
  const nextSignature = collabMessagesSignature(messages);
  syncCollabTypewriterEntries(messages);
  if (!messages.length) {
    renderCollabEmptyState("还没有可展示的协作进展。");
    state.devShelf.collabScroll.userDetached = false;
    state.devShelf.collabScroll.hasNewMessages = false;
    state.devShelf.collabScroll.messageSignature = nextSignature;
    renderCollabNewMessageNotice();
    return;
  }
  updateCollabNewMessageState(messages, scrollSnapshot, shouldStickToBottom);
  renderCollabMessageList(messages);
  restoreCollabChatScroll(scrollSnapshot, { stickToBottom: shouldStickToBottom });
  renderCollabNewMessageNotice();
}

function renderCollabEmptyState(text) {
  const empty = document.createElement("p");
  empty.className = "empty-state";
  empty.textContent = text;
  elements.devShelfCollabChat.replaceChildren(empty);
}

function renderCollabMessageList(messages) {
  const existingByKey = new Map(
    [...elements.devShelfCollabChat.querySelectorAll(".collab-message[data-message-key]")]
      .map((item) => [item.dataset.messageKey, item]),
  );
  const fragment = document.createDocumentFragment();
  for (const message of messages) {
    const displayText = displayTextForCollabMessage(message);
    const typing = Boolean(message.typewriter && displayText !== String(message.text || ""));
    const key = message.key || collabFallbackMessageKey(message);
    message.key = key;
    const item = existingByKey.get(key) || createCollabMessageElement(key);
    updateCollabMessageElement(item, message, displayText, typing);
    fragment.appendChild(item);
  }
  elements.devShelfCollabChat.replaceChildren(fragment);
}

function createCollabMessageElement(key) {
  const item = document.createElement("article");
  item.className = "collab-message";
  item.dataset.messageKey = key;

  const label = document.createElement("strong");
  label.className = "collab-message-title";

  const activityList = document.createElement("div");
  activityList.className = "collab-activity-list hidden";

  const body = document.createElement("div");
  body.className = "collab-message-body";

  item.append(label, activityList, body);
  return item;
}

function updateCollabMessageElement(item, message, displayText, typing) {
  const role = message.role || "assistant";
  const layoutClass = message.layout ? ` collab-message-${message.layout}` : "";
  item.className = `collab-message collab-message-${role}${layoutClass}${typing ? " is-typing" : ""}`;
  item.dataset.messageKey = message.key || "";
  const label = item.querySelector(".collab-message-title");
  if (label && label.textContent !== message.title) {
    label.textContent = message.title;
  }
  renderCollabActivityList(item.querySelector(".collab-activity-list"), message.activities || []);
  const body = item.querySelector(".collab-message-body");
  if (!body || body.dataset.displayText === displayText) {
    return;
  }
  body.dataset.displayText = displayText;
  body.className = "collab-message-body";
  if (role === "assistant" && message.markdown) {
    body.classList.add("markdown-body");
    body.innerHTML = renderMarkdown(displayText || "");
    return;
  }
  body.textContent = displayText;
}

function renderCollabActivityList(container, activities) {
  if (!container) {
    return;
  }
  container.classList.toggle("hidden", !activities.length);
  if (!activities.length) {
    container.replaceChildren();
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const activity of activities) {
    const row = document.createElement("div");
    row.className = `collab-activity collab-activity-${activity.kind || "status"}`;
    row.dataset.activityKey = activity.key || "";
    const label = document.createElement("span");
    label.className = "collab-activity-kind";
    label.textContent = activity.label || labelCollabActivityKind(activity.kind);
    const text = document.createElement("span");
    text.className = "collab-activity-text";
    text.textContent = activity.text || "";
    row.append(label, text);
    fragment.appendChild(row);
  }
  container.replaceChildren(fragment);
}

function buildCollabMessages(detail) {
  const messages = [];

  const feedbackText = elements.devShelfCollabInput?.value?.trim();
  if (feedbackText) {
    messages.push({
      key: "user:draft-feedback",
      role: "user",
      title: "你",
      text: feedbackText,
    });
  }

  const activities = buildCollabActivityItems(
    state.devShelf.gateway.transcript,
    state.devShelf.gateway.events || [],
  );
  let assistantText = collabAssistantMainText(
    state.devShelf.gateway.transcript,
    state.devShelf.gateway.events || [],
  );
  if (!assistantText && gatewayCompletedWithoutProducedOutputs(detail)) {
    assistantText = buildGatewayEmptyOutputAdvice(detail);
  }
  const gatewayStreaming = isGatewayActivelyStreaming();
  if (!assistantText && gatewayStreaming) {
    assistantText = "正在处理...";
  }
  if (assistantText || activities.length) {
    messages.push({
      key: "assistant:gateway-turn",
      role: "assistant",
      title: "pi-agent",
      text: assistantText,
      markdown: true,
      typewriter: gatewayStreaming && Boolean(assistantText),
      layout: "live-turn",
      activities,
    });
  }

  return messages.slice(-80);
}

function collabAssistantMainText(transcript, events) {
  const parts = [];
  const seen = new Set();
  const transcriptMessages = Array.isArray(transcript?.messages) ? transcript.messages : [];
  for (const message of transcriptMessages) {
    if (message?.role !== "assistant" || (message.kind && message.kind !== "message")) {
      continue;
    }
    appendUniqueCollabTextPart(parts, seen, collabTextFromTranscriptMessage(message));
  }
  if (!parts.length) {
    appendUniqueCollabTextPart(parts, seen, collabAssistantTextFromStream(events));
  }
  return parts.join("\n\n");
}

function appendUniqueCollabTextPart(parts, seen, value) {
  const text = cleanCollabAssistantText(value, { preserveFormatting: true });
  const normalized = normalizeCollabMessageText(text);
  if (!text || seen.has(normalized)) {
    return;
  }
  seen.add(normalized);
  parts.push(text);
}

function collabAssistantTextFromStream(events) {
  const parts = [];
  let draft = "";
  const flushDraft = () => {
    const text = cleanCollabAssistantText(draft, { preserveFormatting: true });
    if (text) {
      parts.push(text);
    }
    draft = "";
  };
  for (const event of events || []) {
    const payload = event.payload || {};
    if (event.event_type === "assistant_delta") {
      const delta = payload.delta || "";
      if (!delta || isNoisyCollabText(delta, { allowFragment: true })) {
        continue;
      }
      draft = `${draft}${delta}`;
      continue;
    }
    if (event.event_type === "assistant_message") {
      const text = cleanCollabAssistantText(payload.text || "", { preserveFormatting: true });
      if (!text) {
        continue;
      }
      if (draft && text.includes(draft.trim())) {
        draft = text;
      } else {
        flushDraft();
        draft = text;
      }
    }
  }
  flushDraft();
  return uniqueCollabTextParts(parts).join("\n\n");
}

function uniqueCollabTextParts(parts) {
  const seen = new Set();
  const unique = [];
  for (const part of parts) {
    const normalized = normalizeCollabMessageText(part);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    unique.push(part);
  }
  return unique;
}

function buildCollabActivityItems(transcript, events) {
  const activities = [];
  const seen = new Set();
  const transcriptMessages = Array.isArray(transcript?.messages) ? transcript.messages : [];
  for (const message of transcriptMessages) {
    const activity = collabActivityFromTranscriptMessage(message);
    appendCollabActivityItem(activities, seen, activity);
  }
  for (const event of events || []) {
    const activity = collabActivityFromStreamEvent(event);
    appendCollabActivityItem(activities, seen, activity);
  }
  return activities.slice(-18);
}

function appendCollabActivityItem(activities, seen, activity) {
  if (!activity?.text) {
    return;
  }
  const key = activity.key || `${activity.kind || "activity"}:${normalizeCollabMessageText(activity.text)}`;
  const normalized = normalizeCollabMessageText(activity.text);
  const signature = `${activity.kind || ""}:${normalized}`;
  if (seen.has(key) || seen.has(signature)) {
    return;
  }
  seen.add(key);
  seen.add(signature);
  activities.push({ ...activity, key });
}

function collabActivityFromTranscriptMessage(message) {
  if (!message || message.role === "assistant" && (!message.kind || message.kind === "message")) {
    return null;
  }
  if (message.role === "user") {
    return null;
  }
  const text = collabTextFromTranscriptMessage(message);
  if (!text) {
    return null;
  }
  return {
    key: message.live_key || collabTranscriptMessageKey(message),
    kind: collabActivityKindFromMessage(message),
    text,
  };
}

function collabActivityFromStreamEvent(event) {
  if (!event || event.event_type === "assistant_delta" || event.event_type === "assistant_message") {
    return null;
  }
  const text = summarizeCollabStreamEvent(event);
  if (!text) {
    return null;
  }
  return {
    key: event.event_id || `stream:${event.event_type}:${event.runtime_sequence || event.cursor || ""}`,
    kind: event.event_type,
    text,
  };
}

function collabActivityKindFromMessage(message) {
  if (message.role === "error") {
    return "error";
  }
  if (message.role === "tool") {
    return "tool";
  }
  return message.kind || message.role || "status";
}

function labelCollabActivityKind(kind) {
  const labels = {
    artifact_candidate: "产物",
    error: "错误",
    file_write: "文件",
    running_service: "服务",
    status: "状态",
    system: "状态",
    tool: "工具",
    tool_call: "工具",
    tool_result: "工具",
  };
  return labels[kind] || "状态";
}

function appendUniqueCollabMessages(messages, additions) {
  const existingKeys = new Set(messages.map((message) => message.key).filter(Boolean));
  const existingTexts = new Set(
    messages
      .filter((message) => message.role !== "system")
      .map((message) => normalizeCollabMessageText(message.text)),
  );
  for (const message of additions) {
    const key = message.key || collabFallbackMessageKey(message);
    const normalized = normalizeCollabMessageText(message.text);
    if (existingKeys.has(key) || (normalized && existingTexts.has(normalized))) {
      continue;
    }
    message.key = key;
    existingKeys.add(key);
    if (normalized) {
      existingTexts.add(normalized);
    }
    messages.push(message);
  }
}

function syncCollabTypewriterEntries(messages) {
  const typewriter = state.devShelf.collabTypewriter;
  const activeKeys = new Set();
  for (const message of messages) {
    message.key = message.key || collabFallbackMessageKey(message);
    if (!message.typewriter) {
      continue;
    }
    const key = message.key;
    activeKeys.add(key);
    const targetText = String(message.text || "");
    const existing = typewriter.entries[key];
    if (!existing) {
      typewriter.entries[key] = {
        visibleText: "",
        targetText,
      };
      continue;
    }
    if (existing.targetText !== targetText) {
      if (!targetText.startsWith(existing.visibleText)) {
        existing.visibleText = "";
      }
      existing.targetText = targetText;
    }
  }

  for (const key of Object.keys(typewriter.entries)) {
    if (!activeKeys.has(key)) {
      delete typewriter.entries[key];
    }
  }
  scheduleCollabTypewriterTickIfNeeded();
}

function collabFallbackMessageKey(message) {
  return [
    "message",
    message.role || "",
    message.title || "",
    normalizeCollabMessageText(message.text || "").slice(0, 96),
  ].join(":");
}

function displayTextForCollabMessage(message) {
  if (!message.typewriter) {
    return String(message.text || "");
  }
  const entry = state.devShelf.collabTypewriter.entries[message.key];
  return entry ? entry.visibleText : "";
}

function scheduleCollabTypewriterTickIfNeeded() {
  if (collabTypewriterTimer !== null || !hasPendingCollabTypewriterEntries()) {
    return;
  }
  collabTypewriterTimer = window.setTimeout(() => {
    collabTypewriterTimer = null;
    if (advanceCollabTypewriterEntries()) {
      renderCollabMessages(state.devShelf.detail);
    }
    scheduleCollabTypewriterTickIfNeeded();
  }, COLLAB_TYPEWRITER_INTERVAL_MS);
}

function hasPendingCollabTypewriterEntries() {
  return Object.values(state.devShelf.collabTypewriter.entries)
    .some((entry) => entry.visibleText.length < entry.targetText.length);
}

function advanceCollabTypewriterEntries() {
  let updated = false;
  for (const entry of Object.values(state.devShelf.collabTypewriter.entries)) {
    if (entry.visibleText.length >= entry.targetText.length) {
      continue;
    }
    const remaining = entry.targetText.slice(entry.visibleText.length);
    const step = collabTypewriterStepSize(remaining.length);
    entry.visibleText = entry.targetText.slice(0, entry.visibleText.length + step);
    updated = true;
  }
  return updated;
}

function collabTypewriterStepSize(remainingLength) {
  if (remainingLength > 800) {
    return COLLAB_TYPEWRITER_MAX_CHARS_PER_TICK;
  }
  if (remainingLength > 240) {
    return 3;
  }
  if (remainingLength > 80) {
    return 2;
  }
  return COLLAB_TYPEWRITER_MIN_CHARS_PER_TICK;
}

function hasLiveCollabProgress(messages) {
  return messages.some((message) => (
    message.role === "assistant"
    && message.title === "pi-agent"
    && normalizeCollabMessageText(message.text)
  ));
}

function hasCollabAssistantTranscript(transcript) {
  const transcriptMessages = Array.isArray(transcript?.messages) ? transcript.messages : [];
  return transcriptMessages.some((message) => (
    message?.role === "assistant"
    && Boolean(cleanCollabAssistantText(message.text || ""))
  ));
}

function appendCollabTranscriptMessages(messages, transcript) {
  const transcriptMessages = Array.isArray(transcript?.messages) ? transcript.messages : [];
  if (!transcriptMessages.length) {
    return;
  }

  const existingTexts = new Set(
    messages
      .filter((message) => message.role !== "system")
      .map((message) => normalizeCollabMessageText(message.text)),
  );
  for (const transcriptMessage of transcriptMessages) {
    if (!["assistant", "user", "tool", "system", "error"].includes(transcriptMessage?.role)) {
      continue;
    }
    const isAssistant = transcriptMessage.role === "assistant";
    const text = collabTextFromTranscriptMessage(transcriptMessage);
    const normalized = normalizeCollabMessageText(text);
    if (!text || existingTexts.has(normalized)) {
      continue;
    }
    existingTexts.add(normalized);
    messages.push({
      key: collabTranscriptMessageKey(transcriptMessage),
      role: collabRoleFromTranscriptMessage(transcriptMessage),
      title: collabTitleFromTranscriptMessage(transcriptMessage),
      text,
      markdown: isAssistant,
      typewriter: shouldTypewriterCollabTranscriptMessage(transcriptMessage),
    });
  }
}

function collabTranscriptMessageKey(message) {
  if (message.live_key) {
    return `transcript:live:${message.live_key}`;
  }
  if (message.sequence_start !== undefined && message.sequence_start !== null) {
    return [
      "transcript",
      message.role || "",
      message.kind || "",
      message.sequence_start,
    ].join(":");
  }
  return [
    "transcript",
    message.role || "",
    message.kind || "",
    normalizeCollabMessageText(message.text || "").slice(0, 96),
  ].join(":");
}

function shouldTypewriterCollabTranscriptMessage(message) {
  return Boolean(message?.role === "assistant" && state.devShelf.collabTypewriter.entries[collabTranscriptMessageKey(message)]);
}

function collabRoleFromTranscriptMessage(message) {
  if (message.role === "user") {
    return "user";
  }
  if (message.role === "error") {
    return "error";
  }
  return "assistant";
}

function collabTitleFromTranscriptMessage(message) {
  if (message.role === "user") {
    return "你";
  }
  if (message.role === "tool") {
    return "工具";
  }
  return "pi-agent";
}

function collabTextFromTranscriptMessage(message) {
  if (message.role === "assistant") {
    return cleanCollabAssistantText(message.text || "", { preserveFormatting: true });
  }
  if (message.role === "tool") {
    return summarizeCollabToolTranscriptText(message.text || "");
  }
  if (message.role === "system") {
    return collabSystemText(message.text || "");
  }
  if (message.role === "error") {
    return trimText(message.text || "", 900);
  }
  return trimText(message.text || "", 900);
}

function collabSystemText(value) {
  const text = String(value || "").trim();
  if (!text || text === "开始处理本轮任务") {
    return "";
  }
  return trimText(text, 700);
}

function summarizeCollabToolTranscriptText(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  if (looksLikeRawToolOutput(text)) {
    return "工具结果已返回，原文保留在执行详情。";
  }
  return trimText(text, 240);
}

function looksLikeRawToolOutput(text) {
  const value = String(text || "").trimStart();
  const lineCount = value.split("\n").length;
  if (value.length > 420 || lineCount > 8) {
    return true;
  }
  return [
    "import ",
    "export ",
    "const ",
    "function ",
    "class ",
    "<!DOCTYPE html",
  ].some((prefix) => value.startsWith(prefix));
}

function normalizeCollabMessageText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function buildCollabStreamMessages(events) {
  const messages = [];
  let assistantDraft = null;
  const flushAssistantDraft = () => {
    if (!assistantDraft) {
      return;
    }
    const text = cleanCollabAssistantText(assistantDraft.text);
    if (text) {
      messages.push({
        key: assistantDraft.key,
        role: "assistant",
        title: "pi-agent",
        text,
        markdown: true,
        typewriter: false,
      });
    }
    assistantDraft = null;
  };

  for (const event of events) {
    const payload = event.payload || {};
    if (event.event_type === "assistant_delta") {
      const delta = payload.delta || "";
      if (!delta || isNoisyCollabText(delta, { allowFragment: true })) {
        continue;
      }
      if (!assistantDraft) {
        assistantDraft = { text: "", key: `stream:assistant:${event.event_id || event.runtime_sequence || "live"}` };
      }
      assistantDraft.text = `${assistantDraft.text}${delta}`;
      continue;
    }

    if (event.event_type === "assistant_message") {
      const text = cleanCollabAssistantText(payload.text || "");
      if (text) {
        if (assistantDraft) {
          assistantDraft.text = text;
          flushAssistantDraft();
        } else {
          messages.push({
            key: `stream:assistant:${event.event_id || event.runtime_sequence || normalizeCollabMessageText(text).slice(0, 48)}`,
            role: "assistant",
            title: "pi-agent",
            text,
            markdown: true,
            typewriter: false,
          });
        }
      }
      continue;
    }

    flushAssistantDraft();
    const text = summarizeCollabStreamEvent(event);
    if (!text) {
      continue;
    }
    messages.push({
      key: `stream:${event.event_type}:${event.event_id || event.runtime_sequence || normalizeCollabMessageText(text).slice(0, 48)}`,
      role: event.event_type === "error" ? "error" : "assistant",
      title: "pi-agent",
      text,
      markdown: event.event_type === "running_service",
    });
  }

  flushAssistantDraft();
  return messages;
}

function summarizeCollabStreamEvent(event) {
  const payload = event.payload || {};
  if (event.event_type === "status") {
    const labels = {
      started: "已启动，正在读取当前任务。",
      prompt_accepted: "已收到任务上下文。",
      turn_started: "开始处理本轮任务。",
      turn_completed: "本轮处理完成。",
      completed: "已完成本轮执行。",
      aborted: "已中止本轮执行。",
    };
    return labels[payload.status] || "";
  }
  if (event.event_type === "tool_call") {
    return `正在调用 ${labelCollabToolName(payload.tool_name)}。`;
  }
  if (event.event_type === "tool_result") {
    const name = labelCollabToolName(payload.tool_name);
    return payload.is_error ? `${name} 执行失败。` : `${name} 已返回结果。`;
  }
  if (event.event_type === "file_write") {
    return "已写入文件，产物列表会自动刷新。";
  }
  if (event.event_type === "artifact_candidate") {
    const title = payload.title || labelArtifact(payload.artifact_id);
    return `已生成 ${title}，可在左侧产物列表查看。`;
  }
  if (event.event_type === "running_service") {
    return formatRunningServiceSummary(payload);
  }
  if (event.event_type === "assistant_delta" || event.event_type === "assistant_message") {
    const text = payload.delta || payload.text || "";
    return cleanCollabAssistantText(text);
  }
  if (event.event_type === "error") {
    return payload.message || "执行出错。";
  }
  return "";
}

function cleanCollabAssistantText(value, { preserveFormatting = false } = {}) {
  const text = String(value || "").trim();
  if (isNoisyCollabText(text)) {
    return "";
  }
  return preserveFormatting ? trimMultilineText(text, 6000) : trimText(text, 1800);
}

function isNoisyCollabText(value, { allowFragment = false } = {}) {
  const text = String(value || "").trim();
  if (!text) {
    return true;
  }
  return [
    "packet prompt",
    "required context",
    "required_context",
    "source_output",
    "gateway-event-candidates",
    "需求确认清单模板",
    "需求确认清单模板",
    "模板用于",
    "```json",
    "\"schema_version\"",
    "\"event_type\"",
    "\"runtime_sequence\"",
    "toolCallId",
    "runtime-events",
    "runtime_events",
  ].some((marker) => text.toLowerCase().includes(marker.toLowerCase()));
}

function labelCollabToolName(value) {
  const labels = {
    read: "读取文件",
    write: "写入文件",
    edit: "编辑文件",
    bash: "运行命令",
    shell: "运行命令",
    apply_patch: "应用补丁",
  };
  return labels[value] || value || "工具";
}

function isGatewayActivelyStreaming() {
  const gateway = state.devShelf.gateway;
  return Boolean(
    isWaitingForStartedGatewaySession()
    || gateway.status?.status === "starting"
  );
}

function trimMultilineText(value, limit) {
  const text = String(value || "").trim();
  return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
}

function syncCollabScrollContext(detail) {
  const contextKey = collabScrollContextKey(detail);
  if (state.devShelf.collabScroll.contextKey === contextKey) {
    return;
  }
  state.devShelf.collabScroll.contextKey = contextKey;
  state.devShelf.collabScroll.userDetached = false;
  state.devShelf.collabScroll.messageSignature = null;
  state.devShelf.collabScroll.hasNewMessages = false;
  resetCollabTypewriter(contextKey);
}

function resetCollabTypewriter(contextKey = null) {
  if (collabTypewriterTimer !== null) {
    window.clearTimeout(collabTypewriterTimer);
    collabTypewriterTimer = null;
  }
  state.devShelf.collabTypewriter = {
    contextKey,
    entries: {},
  };
}

function collabScrollContextKey(detail) {
  return [
    detail?.run_id || state.devShelf.selectedRunId || "",
    state.devShelf.gateway.sessionId || "",
  ].join(":");
}

function updateCollabChatScrollIntent() {
  state.devShelf.collabScroll.userDetached = !isScrollableNearBottom(elements.devShelfCollabChat);
  if (!state.devShelf.collabScroll.userDetached) {
    state.devShelf.collabScroll.hasNewMessages = false;
  }
  renderCollabNewMessageNotice();
}

function collabMessagesSignature(messages) {
  return messages
    .map((message) => {
      const text = String(message.text || "");
      return `${message.key || message.role}:${text.length}:${text.slice(-120)}`;
    })
    .join("|");
}

function updateCollabNewMessageState(messages, scrollSnapshot, shouldStickToBottom) {
  const nextSignature = collabMessagesSignature(messages);
  const previousSignature = state.devShelf.collabScroll.messageSignature;
  const userReadingOlderMessages = state.devShelf.collabScroll.userDetached && !scrollSnapshot.nearBottom;
  if (previousSignature && previousSignature !== nextSignature && userReadingOlderMessages && !shouldStickToBottom) {
    state.devShelf.collabScroll.hasNewMessages = true;
  }
  if (shouldStickToBottom || scrollSnapshot.nearBottom) {
    state.devShelf.collabScroll.hasNewMessages = false;
  }
  state.devShelf.collabScroll.messageSignature = nextSignature;
}

function renderCollabNewMessageNotice() {
  elements.devShelfCollabNewMessageButton.classList.toggle(
    "hidden",
    !state.devShelf.collabScroll.hasNewMessages,
  );
}

function scrollCollabChatToLatest() {
  const element = elements.devShelfCollabChat;
  if (!element) {
    return;
  }
  element.scrollTop = element.scrollHeight;
  state.devShelf.collabScroll.userDetached = false;
  state.devShelf.collabScroll.hasNewMessages = false;
  renderCollabNewMessageNotice();
}

function captureScrollablePosition(element) {
  if (!element) {
    return {
      scrollTop: 0,
      nearBottom: true,
    };
  }
  return {
    scrollTop: element.scrollTop,
    nearBottom: isScrollableNearBottom(element),
  };
}

function isScrollableNearBottom(element) {
  if (!element) {
    return true;
  }
  const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
  return distanceFromBottom <= SCROLL_BOTTOM_TOLERANCE_PX;
}

function restoreCollabChatScroll(snapshot, { stickToBottom = false } = {}) {
  window.requestAnimationFrame(() => {
    const element = elements.devShelfCollabChat;
    if (!element) {
      return;
    }
    if (stickToBottom) {
      element.scrollTop = element.scrollHeight;
      state.devShelf.collabScroll.userDetached = false;
      state.devShelf.collabScroll.hasNewMessages = false;
      renderCollabNewMessageNotice();
      return;
    }
    const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
    element.scrollTop = Math.min(snapshot.scrollTop, maxScrollTop);
    state.devShelf.collabScroll.userDetached = !isScrollableNearBottom(element);
    if (!state.devShelf.collabScroll.userDetached) {
      state.devShelf.collabScroll.hasNewMessages = false;
    }
    renderCollabNewMessageNotice();
  });
}

function artifactPreviewContextKey(artifact) {
  return artifactPreviewKey(artifact);
}

function captureArtifactPreviewScroll(artifact) {
  const element = elements.devShelfArtifactPreview;
  if (!element || !state.devShelf.artifactPreviewOpen || !artifact) {
    return null;
  }
  const snapshot = captureScrollablePosition(element);
  return {
    key: artifactPreviewContextKey(artifact),
    scrollTop: snapshot.scrollTop,
    nearBottom: snapshot.nearBottom,
  };
}

function restoreArtifactPreviewScroll(snapshot) {
  if (!snapshot) {
    return;
  }
  window.requestAnimationFrame(() => {
    const artifact = selectedDevShelfArtifact();
    const element = elements.devShelfArtifactPreview;
    if (!element || !artifact || artifactPreviewContextKey(artifact) !== snapshot.key) {
      return;
    }
    const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
    element.scrollTop = Math.min(snapshot.scrollTop, maxScrollTop);
    state.devShelf.artifactPreviewScroll = {
      key: snapshot.key,
      scrollTop: element.scrollTop,
      nearBottom: isScrollableNearBottom(element),
    };
  });
}

function capturePageScrollPosition() {
  return {
    x: window.scrollX,
    y: window.scrollY,
  };
}

function restorePageScrollPosition(snapshot) {
  if (!snapshot) {
    return;
  }
  window.requestAnimationFrame(() => {
    window.scrollTo(snapshot.x, snapshot.y);
  });
}

async function submitCollabFeedback(event) {
  event.preventDefault();
  const feedback = state.devShelf.collabFeedback;
  const text = elements.devShelfCollabInput.value.trim();
  feedback.error = null;
  feedback.message = null;
  if (!state.devShelf.detail) {
    feedback.error = "请选择一个 run。";
    renderCollabPanel(null);
    return;
  }
  if (!text) {
    feedback.error = "请先输入修改意见或补充需求。";
    renderCollabPanel(state.devShelf.detail);
    return;
  }
  if (canAbortGateway()) {
    await abortDevShelfGateway();
    feedback.message = "已请求中止 pi-agent；修改意见保留在输入框里，确认停止后可提交并重新生成。";
    renderCollabPanel(state.devShelf.detail);
    return;
  }

  elements.devShelfCollabSubmitButton.disabled = true;
  feedback.message = "正在提交修改意见并准备重新生成。";
  renderCollabPanel(state.devShelf.detail);

  try {
    const detail = await rejectSelectedArtifactForRevision(text);
    state.devShelf.detail = detail;
    elements.devShelfCollabInput.value = "";
    autoSizeCollabInput();
    feedback.message = "已记录修改意见，正在启动重新生成。";
    await refreshDevShelfSnapshot({ silent: true });
    await startDevShelfGateway();
  } catch (error) {
    feedback.error = error.message;
    feedback.message = null;
  } finally {
    elements.devShelfCollabSubmitButton.disabled = false;
    renderDevShelf();
  }
}

function autoSizeCollabInput() {
  const input = elements.devShelfCollabInput;
  if (!input) {
    return;
  }
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
}

async function rejectSelectedArtifactForRevision(feedbackText) {
  const runId = state.devShelf.selectedRunId;
  const artifact = visibleDevShelfArtifacts()
    .find((item) => item.artifact_id === state.devShelf.selectedArtifactId);
  if (!runId || !artifact) {
    throw new Error("请先选择要修改的产物。");
  }

  if (artifact.source === "gateway_candidate" && artifact.candidate_id) {
    const response = await fetch(
      `/api/dev-shelf/runs/${runId}/gateway/candidates/${encodeURIComponent(artifact.candidate_id)}/revise`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.devShelf.gateway.sessionId,
          feedback: feedbackText,
        }),
      },
    );
    const result = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(result.detail || "提交修改意见失败");
    }
    return result;
  }

  const response = await fetch(
    `/api/dev-shelf/runs/${runId}/artifacts/${encodeURIComponent(artifact.artifact_id)}/revise`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        feedback: feedbackText,
      }),
    },
  );
  const result = await readJsonResponse(response);
  if (!response.ok) {
    throw new Error(result.detail || "提交修改意见失败");
  }
  return result;
}

function canCancelRun(detail) {
  return Boolean(detail && !isTerminalRunStatus(detail.status));
}

function isTerminalRunStatus(status) {
  return status === "completed" || status === "cancelled";
}

function renderGatewayEvents(events) {
  elements.devShelfGatewayEvents.innerHTML = "";
  if (!events.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "当前 Gateway session 还没有可展示事件。";
    elements.devShelfGatewayEvents.appendChild(empty);
    return;
  }
  for (const event of events) {
    const isStreamEvent = Boolean(event.event_type);
    const item = document.createElement("article");
    item.className = `gateway-event gateway-event-${event.event_type || event.kind || "unknown"}`;

    const head = document.createElement("div");
    head.className = "gateway-event-head";

    const title = document.createElement("strong");
    title.textContent = isStreamEvent
      ? `#${event.runtime_sequence || event.cursor || "-"} ${labelWorkbenchStreamEvent(event.event_type)}`
      : `#${event.sequence || "-"} ${labelRuntimeEventKind(event.kind)}`;

    const time = document.createElement("span");
    time.className = "minor-meta";
    time.textContent = formatTime(event.ts);

    const body = document.createElement("p");
    body.textContent = isStreamEvent
      ? summarizeWorkbenchStreamEvent(event)
      : summarizeRuntimeEvent(event);

    const rawDetails = document.createElement("details");
    rawDetails.className = "gateway-event-raw";
    const rawSummary = document.createElement("summary");
    rawSummary.textContent = isStreamEvent ? "事件负载" : "原始事件";
    const rawContent = document.createElement("pre");
    rawContent.textContent = JSON.stringify(isStreamEvent ? {
      event_id: event.event_id,
      payload: event.payload || {},
      source: event.source || {},
    } : event.raw || {}, null, 2);
    rawDetails.append(rawSummary, rawContent);

    head.append(title, time);
    item.append(head, body, rawDetails);
    elements.devShelfGatewayEvents.appendChild(item);
  }
}

function renderGatewaySummary(gateway) {
  elements.devShelfGatewaySummary.innerHTML = "";
  const resultSummary = gateway.result?.payload?.summary || gateway.status?.artifact_result_summary;
  const candidateSummary = gateway.candidates?.payload?.summary || gateway.status?.event_candidate_summary;
  const errorText = latestGatewayErrorText();
  const lines = [
    ["状态", labelGatewayStatus(gateway.status?.status)],
    ["结果", gatewayOutcomeText(state.devShelf.detail, gateway.status)],
    ["pi session", gateway.status?.pi_session_id || "-"],
    ["launch log", gateway.status?.log_path || gateway.control?.lastResponse?.log_path || "-"],
    ["产物", summaryCounts(resultSummary)],
    ["内部候选", summaryCounts(candidateSummary)],
  ];
  if (errorText) {
    lines.push(["最近错误", errorText]);
  }
  for (const [label, value] of lines) {
    const row = document.createElement("div");
    row.className = "gateway-summary-row";
    const name = document.createElement("span");
    name.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value;
    row.append(name, content);
    elements.devShelfGatewaySummary.appendChild(row);
  }
  if (errorText && (gateway.status?.status === "failed" || gatewayCompletedWithoutProducedOutputs(state.devShelf.detail))) {
    const error = document.createElement("pre");
    error.className = "gateway-error-log";
    error.textContent = errorText;
    elements.devShelfGatewaySummary.appendChild(error);
  }
}

function gatewayOutcomeText(detail, status) {
  if (!status) {
    return "-";
  }
  if (status.abort_requested || status.status === "aborted") {
    return "已中止";
  }
  if (status.status === "failed") {
    return "执行失败";
  }
  if (status.status === "starting" || isWaitingForStartedGatewaySession()) {
    return "执行中";
  }
  if (status.status === "completed") {
    if (canRegisterImplementationResult(detail)) {
      return "执行结果待登记";
    }
    if (gatewayCompletedWithoutProducedOutputs(detail)) {
      return "生成未产出，可重新生成";
    }
    const producedCount = gatewayProducedOutputCount();
    if (producedCount && producedCount > 0) {
      return `成功产出 ${producedCount} 个产物`;
    }
    return "已完成";
  }
  return labelGatewayStatus(status.status);
}

function renderDevShelfArtifacts(artifacts) {
  elements.devShelfArtifacts.innerHTML = "";
  if (!artifacts.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "当前任务还没有中间产物。";
    elements.devShelfArtifacts.appendChild(empty);
    return;
  }

  for (const artifact of artifacts) {
    const item = document.createElement("button");
    item.type = "button";
    const classes = ["artifact-list-item"];
    if (artifact.artifact_id === state.devShelf.selectedArtifactId) {
      classes.push("active");
    }
    if (artifact.source === "gateway_candidate") {
      classes.push("reviewable");
    }
    item.className = classes.join(" ");
    item.addEventListener("click", () => {
      if (state.devShelf.selectedArtifactId !== artifact.artifact_id) {
        state.devShelf.artifactAction = defaultArtifactActionState();
        state.devShelf.artifactPreviewScroll = defaultArtifactPreviewScrollState();
        elements.devShelfArtifactFeedbackInput.value = "";
      }
      if (!state.devShelf.artifactPreviewOpen) {
        state.devShelf.artifactPreviewPageScroll = capturePageScrollPosition();
      }
      state.devShelf.selectedArtifactId = artifact.artifact_id;
      state.devShelf.artifactPreviewOpen = true;
      renderDevShelfDetail();
    });

    const head = document.createElement("div");
    head.className = "artifact-list-head";

    const title = document.createElement("strong");
    title.textContent = artifact.title || labelArtifact(artifact.artifact_id);

    const badge = document.createElement("span");
    badge.className = `badge ${artifact.review_required ? "waiting" : "subtle"} mini-badge`;
    badge.textContent = artifact.review_required ? "待查看" : labelArtifactStatus(artifact.status);

    const path = document.createElement("p");
    path.className = "artifact-path";
    path.textContent = artifact.path ? `路径：${artifact.path}` : "未记录路径";

    const meta = document.createElement("p");
    meta.className = "artifact-card-meta";
    meta.textContent = [
      `状态：${labelArtifactStatus(artifact.status)}`,
      artifact.updated_at ? `更新：${formatTime(artifact.updated_at)}` : "更新：-",
      artifactRevisionMeta(artifact),
      artifactPreviewabilityLabel(artifact),
    ].filter(Boolean).join(" · ");

    head.append(title, badge);
    item.append(head, meta, path);
    elements.devShelfArtifacts.appendChild(item);
  }
}

function renderSelectedArtifactPreview(artifacts) {
  const artifact = artifacts.find((item) => item.artifact_id === state.devShelf.selectedArtifactId);
  const scrollSnapshot = state.devShelf.artifactPreviewOpen ? captureArtifactPreviewScroll(artifact) : null;
  if (!artifact) {
    elements.artifactPreviewTitle.textContent = "产物预览";
    elements.devShelfArtifactPreviewMeta.textContent = "-";
    renderArtifactAction(null);
    elements.devShelfArtifactPreview.textContent = "请选择一个产物。";
    restoreArtifactPreviewScroll(scrollSnapshot);
    return;
  }

  elements.artifactPreviewTitle.textContent = artifact.title || labelArtifact(artifact.artifact_id);
  renderArtifactAction(artifact);
  const parts = [
    artifact.source === "gateway_candidate" ? "本次生成，等待查看" : labelArtifactStatus(artifact.status),
  ];
  if (artifact.updated_at) {
    parts.push(`更新于 ${formatTime(artifact.updated_at)}`);
  }
  if (artifact.content_truncated) {
    parts.push("内容已截断");
  }
  elements.devShelfArtifactPreviewMeta.textContent = parts.join(" · ");

  if (artifact.content_error) {
    elements.devShelfArtifactPreview.textContent = artifact.content_error;
    restoreArtifactPreviewScroll(scrollSnapshot);
    return;
  }
  if (!artifact.content) {
    elements.devShelfArtifactPreview.textContent = "当前产物没有可预览内容。";
    restoreArtifactPreviewScroll(scrollSnapshot);
    return;
  }
  const content = String(artifact.content || "");
  if (artifact.content_format === "markdown") {
    elements.devShelfArtifactPreview.innerHTML = renderMarkdown(content);
    restoreArtifactPreviewScroll(scrollSnapshot);
    return;
  }
  elements.devShelfArtifactPreview.innerHTML = `<pre><code>${escapeHtml(content)}</code></pre>`;
  restoreArtifactPreviewScroll(scrollSnapshot);
}

function renderArtifactPreviewModal(artifacts) {
  if (!state.devShelf.artifactPreviewOpen) {
    elements.artifactPreviewModal.classList.add("hidden");
    return;
  }
  const hasSelectedArtifact = artifacts.some((item) => item.artifact_id === state.devShelf.selectedArtifactId);
  elements.artifactPreviewModal.classList.toggle("hidden", !hasSelectedArtifact);
  if (!hasSelectedArtifact) {
    resetArtifactPreviewState();
  }
}

function artifactPreviewKey(artifact) {
  return [
    state.devShelf.selectedRunId || "",
    artifact?.artifact_id || "",
    artifact?.candidate_id || "",
    artifact?.path || "",
  ].join(":");
}

function closeArtifactPreviewModal() {
  const pageScrollSnapshot = state.devShelf.artifactPreviewPageScroll;
  resetArtifactPreviewState();
  renderDevShelfDetail();
  restorePageScrollPosition(pageScrollSnapshot);
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

function canReviseArtifactFromReview(artifact) {
  if (!artifact) {
    return false;
  }
  if (artifact.source === "gateway_candidate" && artifact.candidate_id) {
    return true;
  }
  return ["draft", "in_review", "rejected"].includes(artifact.status);
}

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

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch (error) {
    return { detail: response.statusText || "请求失败" };
  }
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
