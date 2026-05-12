import { state } from './state.js';
import { elements, GATEWAY_STREAM_EVENT_TYPES, renderDevShelf } from './dom.js';
import { loadDevShelfRunDetail } from './api.js';

function handleGatewayProviderChange() {
  renderModelConfigControls();
  saveModelConfigSelection();
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

export { handleGatewayProviderChange, loadGatewaySnapshot };