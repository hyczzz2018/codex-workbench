// Global application state
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

export { state };