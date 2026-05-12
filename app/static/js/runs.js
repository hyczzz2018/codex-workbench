import { state } from './state.js';
import { elements, AUTO_REFRESH_INTERVAL_MS } from './dom.js';
import { loadDevShelfRuns, loadDevShelfRunDetail, loadBackendHealth } from './api.js';

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

function setGatewayView(view) {
  if (!["chat", "events", "summary"].includes(view)) {
    return;
  }
  state.devShelf.gateway.activeView = view;
  renderDevShelfGateway(state.devShelf.detail);
}

export { showProjectCreatePanel, cancelDevShelfRun, handlePrimaryDevShelfAction, continueDevShelfWorkflow, startDevShelfAutoRefresh, setGatewayView };