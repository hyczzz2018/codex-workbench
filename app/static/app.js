const state = {
  devShelf: {
    runs: [],
    selectedRunId: null,
    detail: null,
    loading: false,
    error: null,
    selectedArtifactId: null,
    polling: false,
    autoRefreshInFlight: false,
    autoRefreshError: null,
    lastRefreshedAt: null,
    createRun: {
      busy: false,
      message: null,
      error: null,
      lastRunId: null,
    },
    gateway: {
      status: null,
      events: [],
      result: null,
      candidates: null,
      cursor: 0,
      sessionId: null,
      error: null,
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
let autoRefreshTimer = null;

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
  review_report: "复查报告",
  final_summary: "最终总结",
};

const targetLabels = {
  "template.requirement-confirmation-checklist": "生成需求确认清单",
  "template.existing-project-analysis": "生成现有项目分析",
  "template.spec": "生成 spec",
  "template.reuse-decision": "生成复用判断",
  "template.execution-todo": "生成执行待办",
  "stage.confirmed_requirement": "进入已确认需求",
  "stage.spec_drafting": "进入 spec 起草",
  "stage.reuse_decision": "进入复用判断",
  "stage.implementation_planning": "进入执行待办规划",
  "stage.execution": "进入代码实现",
  "stage.review": "进入复查",
};

const elements = {
  projectCreateForm: document.querySelector("#project-create-form"),
  projectCreateBadge: document.querySelector("#project-create-badge"),
  projectNameInput: document.querySelector("#project-name-input"),
  projectTaskTypeInput: document.querySelector("#project-task-type-input"),
  projectContextInput: document.querySelector("#project-context-input"),
  projectPathInput: document.querySelector("#project-path-input"),
  projectRequirementInput: document.querySelector("#project-requirement-input"),
  projectWorkspaceConfirmedInput: document.querySelector("#project-workspace-confirmed-input"),
  projectCreateButton: document.querySelector("#project-create-button"),
  projectCreateStatus: document.querySelector("#project-create-status"),
  refreshRunsButton: document.querySelector("#refresh-runs-button"),
  autoRefreshStatus: document.querySelector("#auto-refresh-status"),
  devShelfRunList: document.querySelector("#dev-shelf-run-list"),
  devShelfRunBadge: document.querySelector("#dev-shelf-run-badge"),
  devShelfRunId: document.querySelector("#dev-shelf-run-id"),
  devShelfRunStage: document.querySelector("#dev-shelf-run-stage"),
  devShelfRunStatus: document.querySelector("#dev-shelf-run-status"),
  devShelfPacketTarget: document.querySelector("#dev-shelf-packet-target"),
  devShelfRouterStatus: document.querySelector("#dev-shelf-router-status"),
  devShelfGatewayStatus: document.querySelector("#dev-shelf-gateway-status"),
  devShelfGatewaySession: document.querySelector("#dev-shelf-gateway-session"),
  devShelfGatewayModel: document.querySelector("#dev-shelf-gateway-model"),
  devShelfGatewayEventCount: document.querySelector("#dev-shelf-gateway-event-count"),
  devShelfGatewayCandidateCount: document.querySelector("#dev-shelf-gateway-candidate-count"),
  devShelfGatewayEvents: document.querySelector("#dev-shelf-gateway-events"),
  devShelfGatewaySummary: document.querySelector("#dev-shelf-gateway-summary"),
  devShelfGatewayAccount: document.querySelector("#dev-shelf-gateway-account"),
  devShelfGatewayModelControl: document.querySelector("#dev-shelf-gateway-model-control"),
  devShelfGatewayLightMode: document.querySelector("#dev-shelf-gateway-light-mode"),
  devShelfGatewayStartButton: document.querySelector("#dev-shelf-gateway-start-button"),
  devShelfGatewayAbortButton: document.querySelector("#dev-shelf-gateway-abort-button"),
  devShelfGatewayControlStatus: document.querySelector("#dev-shelf-gateway-control-status"),
  devShelfHumanGates: document.querySelector("#dev-shelf-human-gates"),
  devShelfArtifacts: document.querySelector("#dev-shelf-artifacts"),
  devShelfArtifactPreviewMeta: document.querySelector("#dev-shelf-artifact-preview-meta"),
  devShelfArtifactPreview: document.querySelector("#dev-shelf-artifact-preview"),
  devShelfPacketMeta: document.querySelector("#dev-shelf-packet-meta"),
  devShelfPacketContent: document.querySelector("#dev-shelf-packet-content"),
};

elements.projectCreateForm.addEventListener("submit", (event) => createDevShelfRun(event));
elements.refreshRunsButton.addEventListener("click", () => loadDevShelfRuns());
elements.devShelfGatewayStartButton.addEventListener("click", () => startDevShelfGateway());
elements.devShelfGatewayAbortButton.addEventListener("click", () => abortDevShelfGateway());

renderDevShelf();
loadDevShelfRuns();
startDevShelfAutoRefresh();

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
    state.devShelf.selectedRunId = result.run_id;
    state.devShelf.selectedArtifactId = null;
    resetGatewayState();
    await refreshDevShelfSnapshot({ silent: false });
  } catch (error) {
    createRun.error = error.message;
  } finally {
    createRun.busy = false;
    renderDevShelf();
  }
}

function startDevShelfAutoRefresh() {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer);
  }
  state.devShelf.polling = true;
  autoRefreshTimer = window.setInterval(() => {
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
  renderDevShelf();

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
    if (!nextRunId && runs.length) {
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
    renderDevShelf();
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
  ensureSelectedArtifact();
  await loadGatewaySnapshot(runId);
}

function defaultGatewayControlState() {
  return {
    busy: false,
    message: null,
    error: null,
    lastResponse: null,
  };
}

function resetGatewayState({ preserveControl } = {}) {
  const control = preserveControl
    ? (state.devShelf.gateway.control || defaultGatewayControlState())
    : defaultGatewayControlState();
  state.devShelf.gateway = {
    status: null,
    events: [],
    result: null,
    candidates: null,
    cursor: 0,
    sessionId: null,
    error: null,
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
      state.devShelf.gateway.events = [];
      state.devShelf.gateway.cursor = 0;
      state.devShelf.gateway.result = null;
      state.devShelf.gateway.candidates = null;
    }
    state.devShelf.gateway.status = status;
    state.devShelf.gateway.sessionId = status.gateway_session_id;
    state.devShelf.gateway.error = null;

    const params = new URLSearchParams({
      cursor: String(state.devShelf.gateway.cursor || 0),
      limit: "50",
    });
    if (status.gateway_session_id) {
      params.set("session_id", status.gateway_session_id);
    }
    const [eventsResponse, resultResponse, candidatesResponse] = await Promise.all([
      fetch(`/api/dev-shelf/runs/${runId}/gateway/events?${params.toString()}`, { cache: "no-store" }),
      fetch(`/api/dev-shelf/runs/${runId}/gateway/result?session_id=${encodeURIComponent(status.gateway_session_id)}`, {
        cache: "no-store",
      }),
      fetch(`/api/dev-shelf/runs/${runId}/gateway/candidates?session_id=${encodeURIComponent(status.gateway_session_id)}`, {
        cache: "no-store",
      }),
    ]);

    if (eventsResponse.ok) {
      const page = await eventsResponse.json();
      state.devShelf.gateway.events = [...state.devShelf.gateway.events, ...(page.events || [])].slice(-120);
      state.devShelf.gateway.cursor = page.next_cursor || state.devShelf.gateway.cursor;
    }
    if (resultResponse.ok) {
      state.devShelf.gateway.result = await resultResponse.json();
    }
    if (candidatesResponse.ok) {
      state.devShelf.gateway.candidates = await candidatesResponse.json();
    }
  } catch (error) {
    state.devShelf.gateway.error = error.message;
  }
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
      account: elements.devShelfGatewayAccount.value.trim() || "a",
      provider: "openai-codex",
      model: elements.devShelfGatewayModelControl.value.trim() || "gpt-5.4",
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
    state.devShelf.gateway.events = [];
    state.devShelf.gateway.cursor = 0;
    await loadGatewaySnapshot(runId);
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
  const artifacts = state.devShelf.detail?.artifacts || [];
  if (!artifacts.length) {
    state.devShelf.selectedArtifactId = null;
    return;
  }
  if (artifacts.some((item) => item.artifact_id === state.devShelf.selectedArtifactId)) {
    return;
  }
  const waiting = artifacts.find((item) => item.status === "in_review" || item.status === "draft");
  const previewable = artifacts.find((item) => item.content || item.content_error);
  state.devShelf.selectedArtifactId =
    (waiting || previewable || artifacts[0]).artifact_id;
}

function renderDevShelf() {
  renderProjectCreate();
  renderDevShelfRuns();
  renderDevShelfDetail();
  renderAutoRefreshStatus();
  elements.refreshRunsButton.disabled = state.devShelf.loading;
  elements.refreshRunsButton.textContent = state.devShelf.loading ? "读取中" : "刷新";
}

function renderProjectCreate() {
  const createRun = state.devShelf.createRun;
  const disabled = createRun.busy;
  elements.projectNameInput.disabled = disabled;
  elements.projectTaskTypeInput.disabled = disabled;
  elements.projectContextInput.disabled = disabled;
  elements.projectPathInput.disabled = disabled;
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
  elements.projectCreateStatus.textContent = "创建后会自动选中新 run，并显示需求草稿等中间产物。";
  elements.projectCreateStatus.className = "project-create-status";
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
    elements.devShelfRunId.textContent = "-";
    elements.devShelfRunStage.textContent = "-";
    elements.devShelfRunStatus.textContent = "-";
    elements.devShelfPacketTarget.textContent = "-";
    elements.devShelfRouterStatus.textContent = "-";
    renderDevShelfGateway(null);
    elements.devShelfHumanGates.innerHTML = '<p class="empty-state">请选择一个任务。</p>';
    elements.devShelfPacketMeta.textContent = "-";
    elements.devShelfArtifacts.innerHTML = '<p class="empty-state">请选择一个任务。</p>';
    elements.devShelfArtifactPreviewMeta.textContent = "-";
    elements.devShelfArtifactPreview.textContent = "请选择一个产物。";
    elements.devShelfPacketContent.textContent = "请选择一个任务。";
    return;
  }

  elements.devShelfRunBadge.textContent = labelRunStatus(detail.status);
  elements.devShelfRunBadge.className = `badge ${runStatusClass(detail.status)}`;
  elements.devShelfRunId.textContent = detail.run_id;
  elements.devShelfRunStage.textContent = labelStage(detail.current_stage);
  elements.devShelfRunStatus.textContent = labelRunStatus(detail.status);
  elements.devShelfPacketTarget.textContent = buildDevShelfNextAction(detail);
  elements.devShelfRouterStatus.textContent = labelDecision(detail.router?.decision_type);
  renderDevShelfGateway(detail);
  renderDevShelfHumanGates(detail);
  renderDevShelfArtifacts(detail.artifacts || []);
  renderSelectedArtifactPreview(detail.artifacts || []);
  renderDevShelfPacket(detail.latest_packet);
}

function renderDevShelfHumanGates(detail) {
  elements.devShelfHumanGates.innerHTML = "";
  const gates = detail.pending_human_gates || [];

  if (!gates.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = buildDevShelfNextAction(detail);
    elements.devShelfHumanGates.appendChild(empty);
    return;
  }

  for (const gate of gates) {
    const item = document.createElement("article");
    item.className = "human-gate-item";

    const head = document.createElement("div");
    head.className = "artifact-list-head";

    const title = document.createElement("strong");
    title.textContent = gate.label || labelArtifact(gate.artifact_id);

    const badge = document.createElement("span");
    badge.className = "badge waiting mini-badge";
    badge.textContent = labelGateStatus(gate.status);

    const meta = document.createElement("p");
    meta.className = "artifact-path";
    meta.textContent = gate.required_for_stage
      ? `终端处理后将进入：${labelStage(gate.required_for_stage)}`
      : "等待工作终端处理。";

    const note = document.createElement("p");
    note.className = "gate-note";
    note.textContent = "请回到工作终端完成确认和继续执行。网页仅展示当前待确认项。";

    head.append(title, badge);
    item.append(head, meta, note);
    elements.devShelfHumanGates.appendChild(item);
  }
}

function renderDevShelfGateway(detail) {
  const gateway = state.devShelf.gateway;
  const status = detail ? gateway.status : null;
  if (!status) {
    elements.devShelfGatewayStatus.textContent = gateway.error || "暂无";
    elements.devShelfGatewayStatus.className = "badge subtle mini-badge";
    elements.devShelfGatewaySession.textContent = "-";
    elements.devShelfGatewayModel.textContent = "-";
    elements.devShelfGatewayEventCount.textContent = "-";
    elements.devShelfGatewayCandidateCount.textContent = "-";
    elements.devShelfGatewayEvents.innerHTML = `<p class="empty-state">${gateway.error || "暂无 Gateway session。"}</p>`;
    elements.devShelfGatewaySummary.innerHTML = '<p class="empty-state">暂无 Gateway 结果。</p>';
    renderGatewayControls(detail);
    return;
  }

  elements.devShelfGatewayStatus.textContent = labelGatewayStatus(status.status);
  elements.devShelfGatewayStatus.className = `badge ${gatewayStatusClass(status.status)} mini-badge`;
  elements.devShelfGatewaySession.textContent = status.gateway_session_id || "-";
  elements.devShelfGatewayModel.textContent =
    [status.provider, status.model].filter(Boolean).join(" / ") || "-";
  elements.devShelfGatewayEventCount.textContent = String(status.event_count ?? "-");
  const candidateCount = status.event_candidate_summary?.candidate_count ?? 0;
  elements.devShelfGatewayCandidateCount.textContent = String(candidateCount);
  renderGatewayEvents(gateway.events || []);
  renderGatewaySummary(gateway);
  renderGatewayControls(detail);
}

function renderGatewayControls(detail) {
  const control = state.devShelf.gateway.control || defaultGatewayControlState();
  const disabled = !detail || control.busy;
  elements.devShelfGatewayAccount.disabled = disabled;
  elements.devShelfGatewayModelControl.disabled = disabled;
  elements.devShelfGatewayLightMode.disabled = disabled;
  elements.devShelfGatewayStartButton.disabled = disabled;
  elements.devShelfGatewayAbortButton.disabled = disabled;
  elements.devShelfGatewayStartButton.textContent = control.busy ? "处理中" : "启动";
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
  elements.devShelfGatewayControlStatus.textContent = "使用当前 run 的最新 execution packet 启动 pi-agent。";
  elements.devShelfGatewayControlStatus.className = "gateway-control-status";
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
    const item = document.createElement("article");
    item.className = `gateway-event gateway-event-${event.kind || "unknown"}`;

    const head = document.createElement("div");
    head.className = "gateway-event-head";

    const title = document.createElement("strong");
    title.textContent = `#${event.sequence || "-"} ${labelRuntimeEventKind(event.kind)}`;

    const time = document.createElement("span");
    time.className = "minor-meta";
    time.textContent = formatTime(event.ts);

    const body = document.createElement("p");
    body.textContent = summarizeRuntimeEvent(event);

    head.append(title, time);
    item.append(head, body);
    elements.devShelfGatewayEvents.appendChild(item);
  }
}

function renderGatewaySummary(gateway) {
  elements.devShelfGatewaySummary.innerHTML = "";
  const resultSummary = gateway.result?.payload?.summary || gateway.status?.artifact_result_summary;
  const candidateSummary = gateway.candidates?.payload?.summary || gateway.status?.event_candidate_summary;
  const lines = [
    ["状态", labelGatewayStatus(gateway.status?.status)],
    ["pi session", gateway.status?.pi_session_id || "-"],
    ["产物", summaryCounts(resultSummary)],
    ["候选", summaryCounts(candidateSummary)],
  ];
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
    item.className =
      `artifact-list-item ${artifact.artifact_id === state.devShelf.selectedArtifactId ? "active" : ""}`;
    item.addEventListener("click", () => {
      state.devShelf.selectedArtifactId = artifact.artifact_id;
      renderDevShelfDetail();
    });

    const head = document.createElement("div");
    head.className = "artifact-list-head";

    const title = document.createElement("strong");
    title.textContent = artifact.title || labelArtifact(artifact.artifact_id);

    const badge = document.createElement("span");
    badge.className = "badge subtle mini-badge";
    badge.textContent = labelArtifactStatus(artifact.status);

    const path = document.createElement("p");
    path.className = "artifact-path";
    path.textContent = artifact.path ? `路径：${artifact.path}` : "未记录路径";

    head.append(title, badge);
    item.append(head, path);
    elements.devShelfArtifacts.appendChild(item);
  }
}

function renderSelectedArtifactPreview(artifacts) {
  const artifact = artifacts.find((item) => item.artifact_id === state.devShelf.selectedArtifactId);
  if (!artifact) {
    elements.devShelfArtifactPreviewMeta.textContent = "-";
    elements.devShelfArtifactPreview.textContent = "请选择一个产物。";
    return;
  }

  const parts = [labelArtifactStatus(artifact.status)];
  if (artifact.updated_at) {
    parts.push(`更新于 ${formatTime(artifact.updated_at)}`);
  }
  if (artifact.content_truncated) {
    parts.push("内容已截断");
  }
  elements.devShelfArtifactPreviewMeta.textContent = parts.join(" · ");

  if (artifact.content_error) {
    elements.devShelfArtifactPreview.textContent = artifact.content_error;
    return;
  }
  if (!artifact.content) {
    elements.devShelfArtifactPreview.textContent = "当前产物没有可预览内容。";
    return;
  }
  if (artifact.content_format === "markdown") {
    elements.devShelfArtifactPreview.innerHTML = renderMarkdown(artifact.content);
    return;
  }
  elements.devShelfArtifactPreview.innerHTML = `<pre><code>${escapeHtml(artifact.content)}</code></pre>`;
}

function renderDevShelfPacket(packet) {
  if (!packet) {
    elements.devShelfPacketMeta.textContent = "暂无推进建议。";
    elements.devShelfPacketContent.textContent = "当前任务还没有可展示的推进建议。";
    return;
  }

  elements.devShelfPacketMeta.textContent =
    `第 ${packet.sequence || "-"} 轮 · ${labelDecision(packet.decision_type)} · ${labelPacketReady(packet.ready)}`;

  const summary = buildPacketSummary(packet);
  elements.devShelfPacketContent.innerHTML = renderMarkdown(summary);
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
  if (raw.command) {
    return `command: ${raw.command}${raw.success === false ? " failed" : ""}`;
  }
  if (raw.type) {
    return `type: ${raw.type}`;
  }
  const text = JSON.stringify(raw);
  return text.length > 220 ? `${text.slice(0, 220)}...` : text;
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
  if (typeof summary.candidate_count === "number") {
    parts.push(`候选 ${summary.candidate_count}`);
  }
  if (typeof summary.skipped_count === "number") {
    parts.push(`跳过 ${summary.skipped_count}`);
  }
  return parts.join(" · ") || "-";
}

function buildDevShelfNextAction(detail) {
  const gates = detail.pending_human_gates || [];
  if (gates.length) {
    return `等待人工确认：${gates.map((gate) => gate.label || labelArtifact(gate.artifact_id)).join("、")}。请回到工作终端处理。`;
  }
  if (detail.status === "completed") {
    return "本轮任务已完成。";
  }
  const decision = detail.router?.decision_type || detail.latest_packet?.decision_type;
  if (decision === "no_route") {
    return "当前暂无可推进步骤。";
  }
  const target = detail.router?.target || detail.latest_packet?.target;
  if (target) {
    return `下一步建议：${formatTarget(target)}`;
  }
  if (detail.status === "ready_for_next_stage") {
    return "等待工作终端继续推进下一阶段。";
  }
  return "等待终端流程推进。";
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

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      flushList();
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
      continue;
    }

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

    flushList();
    chunks.push(`<p>${inlineMarkdown(line)}</p>`);
  }

  flushList();
  flushCode();

  return chunks.join("") || "<p>当前阶段还没有产物。</p>";
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
