import { state } from './state.js';
import { elements } from './dom.js';
import { loadDevShelfRunDetail, readJsonResponse } from './api.js';

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

function canReviseArtifactFromReview(artifact) {
  if (!artifact) {
    return false;
  }
  if (artifact.source === "gateway_candidate" && artifact.candidate_id) {
    return true;
  }
  return ["draft", "in_review", "rejected"].includes(artifact.status);
}

export { confirmSelectedGatewayArtifact, toggleArtifactFeedbackInput, submitArtifactRevisionFromReview };