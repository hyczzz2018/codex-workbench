import { state } from './state.js';
import { elements, AUTO_REFRESH_INTERVAL_MS } from './dom.js';

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

function markBackendHealthUnavailable(error) {
  state.devShelf.backendHealth = {
    status: "offline",
    message: `后端连接失败：${error?.message || "请检查服务和端口"}。日志 /tmp/codex-workbench.log`,
    logPath: "/tmp/codex-workbench.log",
    checkedAt: new Date(),
  };
}

async function loadDevShelfRuns() {
  await refreshDevShelfSnapshot({ silent: false });
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

async function readJsonResponse(response) {
  try {
    return await response.json();
  } catch (error) {
    return { detail: response.statusText || "请求失败" };
  }
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

export { loadModelConfig, loadAvailableModels, loadBackendHealth, loadDevShelfRuns, loadDevShelfRunDetail, loadProjectDirectories, createProjectDirectory, saveModelConfigSelection, createDevShelfRun, openProjectDirectoryPicker, closeProjectDirectoryPicker, chooseCurrentProjectDirectory, readJsonResponse };