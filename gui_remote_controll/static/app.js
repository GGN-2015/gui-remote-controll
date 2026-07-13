(() => {
  "use strict";

  const stage = document.querySelector("#desktop-stage");
  const screen = document.querySelector("#screen");
  const inputSink = document.querySelector("#input-sink");
  const appTitle = document.querySelector("#app-title");
  const connectionState = document.querySelector("#connection-state");
  const controlPermission = document.querySelector("#control-permission");
  const controlPermissionLabel = document.querySelector("#control-permission-label");
  const stageMessage = document.querySelector("#stage-message");
  const stageTitle = document.querySelector("#stage-title");
  const stageDetail = document.querySelector("#stage-detail");
  const screenInfo = document.querySelector("#screen-info");
  const modeInfo = document.querySelector("#mode-info");
  const monitorSelect = document.querySelector("#monitor-select");
  const fitButton = document.querySelector("#fit-button");
  const actualButton = document.querySelector("#actual-button");
  const fullscreenButton = document.querySelector("#fullscreen-button");
  const imeButton = document.querySelector("#ime-button");
  const clipboardButton = document.querySelector("#clipboard-button");
  const clipboardSyncControl = document.querySelector("#clipboard-sync-control");
  const clipboardSyncToggle = document.querySelector("#clipboard-sync-toggle");
  const clipboardDialog = document.querySelector("#clipboard-dialog");
  const clipboardText = document.querySelector("#clipboard-text");
  const toast = document.querySelector("#toast");

  const CLIPBOARD_SYNC_STORAGE_KEY = "gui-remote-clipboard-sync";
  const CLIPBOARD_SYNC_INTERVAL_MS = 1000;

  let socket = null;
  let reconnectTimer = null;
  let reconnectDelay = 500;
  let currentFrameUrl = null;
  let viewOnly = false;
  let canControl = false;
  let imeSupported = false;
  let imeEnabled = null;
  let imeDetail = "IME status is unavailable.";
  let imePending = false;
  let clipboardEnabled = false;
  let composing = false;
  let pendingPointer = null;
  let pointerFrame = null;
  let lastPointer = { x: 0.5, y: 0.5 };
  let toastTimer = null;
  let clipboardSyncTimer = null;
  let clipboardSyncActive = false;
  let clipboardSyncBusy = false;
  let clipboardStartupPermissionAttempted = false;
  let clipboardPermissionObserved = false;
  let clipboardNoticeShown = false;
  let clipboardChangeListening = false;
  let applyingServerClipboard = false;
  let serverClipboardWriteRunning = false;
  let pendingServerClipboard = null;
  let lastClientClipboard = null;
  let lastServerClipboard = null;
  let lastServerClipboardDigest = null;
  let serverClipboardBaselinePending = false;
  let clientChangedBeforeServerBaseline = false;
  let clipboardRequestSequence = 0;
  const heldKeys = new Map();
  const heldButtons = new Set();
  const clipboardWriteOrigins = new Map();

  function setConnection(label, state) {
    connectionState.textContent = label;
    connectionState.dataset.state = state;
  }

  function setClientTitle(title) {
    if (typeof title !== "string" || !title) return;
    document.title = title;
    appTitle.textContent = title;
  }

  function updateControlState(control) {
    const state = ["available", "local_active", "restricted"].includes(control?.state)
      ? control.state
      : "restricted";
    const labels = {
      available: "Control available",
      local_active: "Server is operating",
      restricted: "Control access restricted",
    };
    canControl = state === "available" && !viewOnly;
    controlPermission.dataset.state = state;
    controlPermissionLabel.textContent = labels[state];
    controlPermission.title = control?.detail || labels[state];
    modeInfo.textContent = labels[state];
    if (!canControl) {
      imePending = false;
      releaseLocalState(false);
    }
    refreshImeButton();
  }

  function updateImeState(ime) {
    imePending = false;
    imeSupported = Boolean(ime?.supported);
    imeEnabled = typeof ime?.enabled === "boolean" ? ime.enabled : null;
    imeDetail = ime?.detail || "IME status is unavailable.";
    refreshImeButton();
  }

  function refreshImeButton() {
    const known = imeSupported && typeof imeEnabled === "boolean";
    imeButton.textContent = known ? `IME: ${imeEnabled ? "On" : "Off"}` : "IME unavailable";
    imeButton.disabled = !canControl || !known || imePending;
    imeButton.setAttribute("aria-pressed", String(Boolean(imeEnabled)));
    imeButton.title = known
      ? `${imeDetail}. Activate to turn the server IME ${imeEnabled ? "off" : "on"}.`
      : imeDetail;
  }

  function setStageMessage(title, detail) {
    stageTitle.textContent = title;
    stageDetail.textContent = detail;
    stageMessage.classList.remove("hidden");
  }

  function hideStageMessage() {
    stageMessage.classList.add("hidden");
  }

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 2600);
  }

  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  function connect() {
    window.clearTimeout(reconnectTimer);
    setConnection("Connecting", "connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    socket.binaryType = "blob";

    socket.addEventListener("open", () => {
      reconnectDelay = 500;
      setConnection("Connected", "connected");
    });

    socket.addEventListener("message", (event) => {
      if (event.data instanceof Blob) {
        showFrame(event.data);
        return;
      }
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        showToast("The server sent an invalid message.");
        return;
      }
      handleServerMessage(message);
    });

    socket.addEventListener("close", async (event) => {
      releaseLocalState(false);
      updateControlState({
        state: "restricted",
        detail: "The WebSocket connection is closed.",
      });
      stopClipboardSyncRuntime();
      monitorSelect.disabled = true;
      clipboardButton.disabled = true;
      clipboardSyncToggle.disabled = true;
      if (event.code === 4401) {
        window.location.assign("/auth");
        return;
      }
      try {
        const status = await window.fetch("/api/status", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (status.status === 401) {
          window.location.assign("/auth");
          return;
        }
      } catch {
        // A network outage is handled by the reconnect path below.
      }
      if (event.code === 4429) {
        setConnection("Server busy", "error");
        setStageMessage("Server busy", "The maximum number of remote clients is connected");
      } else if (!stageMessage.dataset.fatal) {
        setConnection("Disconnected", "disconnected");
        if (!screen.classList.contains("ready")) {
          setStageMessage("Disconnected", "Reconnecting to the remote desktop");
        }
      }
      reconnectTimer = window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(8000, reconnectDelay * 1.8);
    });

    socket.addEventListener("error", () => {
      setConnection("Connection error", "error");
    });
  }

  function handleServerMessage(message) {
    switch (message.type) {
      case "hello":
        viewOnly = Boolean(message.viewOnly);
        clipboardEnabled = Boolean(message.clipboard);
        setClientTitle(message.title);
        updateImeState(message.ime);
        updateControlState(message.control);
        configureClipboardAccess();
        populateScreens(message.screens, message.monitor);
        break;
      case "control_state":
        updateControlState(message);
        break;
      case "ime_state":
        updateImeState(message);
        showToast(`Server IME is now ${message.enabled ? "on" : "off"}.`);
        break;
      case "screen":
        screenInfo.textContent = `${message.name} · ${message.width} × ${message.height}`;
        break;
      case "clipboard":
        handleServerClipboard(message);
        break;
      case "clipboard_unchanged":
        if (message.digest) lastServerClipboardDigest = message.digest;
        break;
      case "clipboard_saved":
        handleClipboardSaved(message);
        break;
      case "error":
        if (message.requestId) clipboardWriteOrigins.delete(message.requestId);
        imePending = false;
        refreshImeButton();
        showToast(message.message || "Remote operation failed.");
        break;
      case "fatal":
        stageMessage.dataset.fatal = "true";
        setConnection("Desktop unavailable", "error");
        setStageMessage("Desktop unavailable", message.message || "The desktop could not be opened");
        break;
      default:
        break;
    }
  }

  function populateScreens(screens, selected) {
    monitorSelect.replaceChildren();
    for (const item of screens || []) {
      const option = document.createElement("option");
      option.value = String(item.index);
      option.textContent = `${item.name} (${item.width} × ${item.height})`;
      option.selected = item.index === selected;
      monitorSelect.append(option);
    }
    monitorSelect.disabled = monitorSelect.options.length < 2;
  }

  function clipboardApiAvailable() {
    return Boolean(
      window.isSecureContext
      && navigator.clipboard
      && typeof navigator.clipboard.readText === "function"
      && typeof navigator.clipboard.writeText === "function"
    );
  }

  function readClipboardPreference() {
    try {
      return window.localStorage.getItem(CLIPBOARD_SYNC_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  }

  function writeClipboardPreference(enabled) {
    try {
      window.localStorage.setItem(CLIPBOARD_SYNC_STORAGE_KEY, String(enabled));
    } catch {
      // Private browsing modes may make local storage unavailable.
    }
  }

  function showClipboardNotice(message) {
    if (clipboardNoticeShown) return;
    clipboardNoticeShown = true;
    showToast(message);
  }

  async function observeClipboardPermission() {
    if (clipboardPermissionObserved || !navigator.permissions?.query) return;
    clipboardPermissionObserved = true;
    try {
      const permission = await navigator.permissions.query({ name: "clipboard-read" });
      permission.addEventListener("change", () => {
        if (permission.state === "denied" && clipboardSyncActive) {
          disableClipboardSync("Clipboard permission was revoked.");
        }
      });
    } catch {
      // Firefox and Safari do not expose Chromium's clipboard permission names.
    }
  }

  async function requestClipboardAccess(notifyFailure) {
    if (!clipboardApiAvailable()) {
      if (notifyFailure) {
        showClipboardNotice("Automatic clipboard sync requires HTTPS or localhost.");
      }
      return { ok: false, text: "" };
    }
    await observeClipboardPermission();
    try {
      const text = await navigator.clipboard.readText();
      clipboardNoticeShown = false;
      return { ok: true, text };
    } catch {
      if (notifyFailure) {
        showClipboardNotice("Allow clipboard access to enable automatic sync.");
      }
      return { ok: false, text: "" };
    }
  }

  async function configureClipboardAccess() {
    stopClipboardSyncRuntime();
    clipboardButton.disabled = !clipboardEnabled;
    clipboardSyncToggle.checked = readClipboardPreference();

    if (!clipboardEnabled) {
      clipboardSyncToggle.disabled = true;
      clipboardSyncToggle.checked = false;
      clipboardSyncControl.title = "Clipboard synchronization is disabled by the server.";
      return;
    }
    if (!clipboardApiAvailable()) {
      clipboardSyncToggle.disabled = true;
      clipboardSyncToggle.checked = false;
      clipboardSyncControl.title = "Automatic sync requires HTTPS or localhost.";
      showClipboardNotice("Automatic clipboard sync requires HTTPS or localhost.");
      return;
    }

    clipboardSyncToggle.disabled = true;
    clipboardSyncControl.title = "Synchronize plain text while this tab is active.";
    if (clipboardStartupPermissionAttempted && !clipboardSyncToggle.checked) {
      clipboardSyncToggle.disabled = false;
      return;
    }

    clipboardStartupPermissionAttempted = true;
    const access = await requestClipboardAccess(true);
    clipboardSyncToggle.disabled = false;
    if (!access.ok) {
      clipboardSyncToggle.checked = false;
      writeClipboardPreference(false);
      return;
    }
    if (clipboardSyncToggle.checked && socket?.readyState === WebSocket.OPEN) {
      startClipboardSync(access.text);
    }
  }

  function clipboardChangeEventsAvailable() {
    return Boolean(
      navigator.clipboard
      && "onclipboardchange" in navigator.clipboard
      && typeof navigator.clipboard.addEventListener === "function"
    );
  }

  function startClipboardSync(initialClientText) {
    stopClipboardSyncRuntime();
    clipboardSyncActive = true;
    clipboardSyncToggle.checked = true;
    lastClientClipboard = initialClientText;
    lastServerClipboard = null;
    lastServerClipboardDigest = null;
    serverClipboardBaselinePending = true;
    clientChangedBeforeServerBaseline = false;
    pendingServerClipboard = null;

    if (clipboardChangeEventsAvailable()) {
      navigator.clipboard.addEventListener("clipboardchange", handleClipboardChange);
      clipboardChangeListening = true;
    }
    send({ type: "clipboard_get" });
    clipboardSyncTimer = window.setInterval(clipboardSyncTick, CLIPBOARD_SYNC_INTERVAL_MS);
  }

  function stopClipboardSyncRuntime() {
    window.clearInterval(clipboardSyncTimer);
    clipboardSyncTimer = null;
    clipboardSyncActive = false;
    clipboardSyncBusy = false;
    applyingServerClipboard = false;
    pendingServerClipboard = null;
    lastClientClipboard = null;
    lastServerClipboard = null;
    lastServerClipboardDigest = null;
    serverClipboardBaselinePending = false;
    clientChangedBeforeServerBaseline = false;
    clipboardWriteOrigins.clear();
    if (clipboardChangeListening) {
      navigator.clipboard.removeEventListener("clipboardchange", handleClipboardChange);
      clipboardChangeListening = false;
    }
  }

  function disableClipboardSync(message) {
    stopClipboardSyncRuntime();
    clipboardSyncToggle.checked = false;
    writeClipboardPreference(false);
    showClipboardNotice(message);
  }

  async function handleClipboardChange() {
    if (applyingServerClipboard) return;
    await syncClientClipboard();
  }

  async function clipboardSyncTick(forceClientRead = false) {
    if (
      !clipboardSyncActive
      || socket?.readyState !== WebSocket.OPEN
      || document.hidden
      || !document.hasFocus()
    ) {
      return;
    }
    if (forceClientRead || !clipboardChangeListening) {
      await syncClientClipboard();
    }
    const request = { type: "clipboard_get" };
    if (lastServerClipboardDigest) request.knownDigest = lastServerClipboardDigest;
    send(request);
  }

  async function syncClientClipboard() {
    if (!clipboardSyncActive || clipboardSyncBusy || applyingServerClipboard) return;
    clipboardSyncBusy = true;
    try {
      const text = await navigator.clipboard.readText();
      publishClientClipboard(text);
    } catch {
      disableClipboardSync("Clipboard read permission is no longer available.");
    } finally {
      clipboardSyncBusy = false;
    }
  }

  function publishClientClipboard(text) {
    if (!clipboardSyncActive || text === lastClientClipboard) return;
    if (serverClipboardBaselinePending) clientChangedBeforeServerBaseline = true;
    lastClientClipboard = text;
    lastServerClipboard = text;
    lastServerClipboardDigest = null;
    sendClipboardSet(text, "automatic");
  }

  function sendClipboardSet(text, origin) {
    const requestId = `clipboard-${++clipboardRequestSequence}`;
    if (send({ type: "clipboard_set", text, requestId })) {
      clipboardWriteOrigins.set(requestId, origin);
    }
  }

  function handleClipboardSaved(message) {
    if (message.digest) lastServerClipboardDigest = message.digest;
    const origin = clipboardWriteOrigins.get(message.requestId);
    if (message.requestId) clipboardWriteOrigins.delete(message.requestId);
    if (origin !== "automatic") showToast("Remote clipboard updated.");
  }

  function handleServerClipboard(message) {
    const text = typeof message.text === "string" ? message.text : "";
    clipboardText.value = text;
    if (!clipboardSyncActive) return;

    if (serverClipboardBaselinePending) {
      serverClipboardBaselinePending = false;
      if (clientChangedBeforeServerBaseline) {
        lastServerClipboard = lastClientClipboard;
        lastServerClipboardDigest = null;
        return;
      }
      lastServerClipboard = text;
      lastServerClipboardDigest = message.digest || null;
      return;
    }
    if (text === lastServerClipboard || text === lastClientClipboard) {
      lastServerClipboard = text;
      lastServerClipboardDigest = message.digest || lastServerClipboardDigest;
      return;
    }
    pendingServerClipboard = { text, digest: message.digest || null };
    flushPendingServerClipboard();
  }

  async function flushPendingServerClipboard() {
    if (serverClipboardWriteRunning) return;
    serverClipboardWriteRunning = true;
    try {
      while (pendingServerClipboard && clipboardSyncActive) {
        const next = pendingServerClipboard;
        pendingServerClipboard = null;
        try {
          applyingServerClipboard = true;
          await navigator.clipboard.writeText(next.text);
          lastClientClipboard = next.text;
          lastServerClipboard = next.text;
          lastServerClipboardDigest = next.digest;
          clipboardNoticeShown = false;
        } catch {
          disableClipboardSync("Browser clipboard write permission is no longer available.");
          return;
        } finally {
          applyingServerClipboard = false;
        }
      }
    } finally {
      serverClipboardWriteRunning = false;
    }
  }

  function showFrame(blob) {
    const nextUrl = URL.createObjectURL(blob);
    const previousUrl = currentFrameUrl;
    currentFrameUrl = nextUrl;
    screen.addEventListener("load", () => {
      if (previousUrl) {
        URL.revokeObjectURL(previousUrl);
      }
      screen.classList.add("ready");
      hideStageMessage();
    }, { once: true });
    screen.src = nextUrl;
  }

  function pointerCoordinates(event) {
    const bounds = screen.getBoundingClientRect();
    if (!bounds.width || !bounds.height) {
      return null;
    }
    lastPointer = {
      x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)),
    };
    return lastPointer;
  }

  function pointerButton(button) {
    if (button === 1) return "middle";
    if (button === 2) return "right";
    return "left";
  }

  function focusRemoteInput() {
    if (canControl) {
      inputSink.focus({ preventScroll: true });
    }
  }

  screen.addEventListener("pointermove", (event) => {
    if (!canControl) return;
    const point = pointerCoordinates(event);
    if (!point) return;
    pendingPointer = { type: "pointer", event: "move", ...point };
    if (pointerFrame === null) {
      pointerFrame = window.requestAnimationFrame(() => {
        if (canControl && pendingPointer) send(pendingPointer);
        pendingPointer = null;
        pointerFrame = null;
      });
    }
  });

  screen.addEventListener("pointerdown", (event) => {
    if (!canControl) return;
    event.preventDefault();
    focusRemoteInput();
    screen.setPointerCapture(event.pointerId);
    const point = pointerCoordinates(event);
    const button = pointerButton(event.button);
    if (point) {
      heldButtons.add(button);
      send({ type: "pointer", event: "down", button, ...point });
    }
  });

  screen.addEventListener("pointerup", (event) => {
    if (!canControl) return;
    event.preventDefault();
    const point = pointerCoordinates(event);
    const button = pointerButton(event.button);
    heldButtons.delete(button);
    if (point) send({ type: "pointer", event: "up", button, ...point });
  });

  screen.addEventListener("pointercancel", () => releaseLocalState(true));
  screen.addEventListener("contextmenu", (event) => event.preventDefault());
  screen.addEventListener("dragstart", (event) => event.preventDefault());
  screen.addEventListener("wheel", (event) => {
    if (!canControl) return;
    event.preventDefault();
    const divisor = event.deltaMode === WheelEvent.DOM_DELTA_PIXEL ? 100 : 3;
    send({
      type: "wheel",
      dx: Math.max(-20, Math.min(20, event.deltaX / divisor)),
      dy: Math.max(-20, Math.min(20, event.deltaY / divisor)),
    });
  }, { passive: false });

  inputSink.addEventListener("keydown", (event) => {
    if (!canControl || event.isComposing || event.key === "Process" || event.key === "Dead") return;
    const pasteShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    if (pasteShortcut) return;
    const printable = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
    if (printable) return;
    event.preventDefault();
    heldKeys.set(event.code || event.key, event.key);
    send({ type: "key", event: "down", key: event.key, code: event.code, repeat: event.repeat });
  });

  inputSink.addEventListener("keyup", (event) => {
    if (!canControl || event.isComposing || event.key === "Process" || event.key === "Dead") return;
    const pasteShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    if (pasteShortcut) return;
    const keyId = event.code || event.key;
    if (!heldKeys.has(keyId) && event.key.length === 1) return;
    event.preventDefault();
    heldKeys.delete(keyId);
    send({ type: "key", event: "up", key: event.key, code: event.code, repeat: false });
  });

  inputSink.addEventListener("compositionstart", () => {
    composing = true;
  });

  inputSink.addEventListener("compositionend", () => {
    composing = false;
    queueMicrotask(flushTextInput);
  });

  inputSink.addEventListener("input", () => {
    if (!composing) queueMicrotask(flushTextInput);
  });

  inputSink.addEventListener("paste", (event) => {
    event.preventDefault();
    if (!canControl) return;
    const text = event.clipboardData?.getData("text/plain") || "";
    if (text) send({ type: "text", text });
  });

  function flushTextInput() {
    if (!canControl) {
      inputSink.value = "";
      return;
    }
    if (composing || !inputSink.value) return;
    send({ type: "text", text: inputSink.value });
    inputSink.value = "";
  }

  function releaseLocalState(notifyServer) {
    if (notifyServer) {
      for (const key of heldKeys.values()) {
        send({ type: "key", event: "up", key, code: "", repeat: false });
      }
      for (const button of heldButtons) {
        send({ type: "pointer", event: "up", button, ...lastPointer });
      }
    }
    heldKeys.clear();
    heldButtons.clear();
  }

  window.addEventListener("blur", () => releaseLocalState(true));
  window.addEventListener("focus", () => clipboardSyncTick(true));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      releaseLocalState(true);
    } else {
      clipboardSyncTick(true);
    }
  });

  monitorSelect.addEventListener("change", () => {
    send({ type: "monitor", index: Number(monitorSelect.value) });
    focusRemoteInput();
  });

  function setScaleMode(mode) {
    const fit = mode === "fit";
    stage.classList.toggle("fit-mode", fit);
    stage.classList.toggle("actual-mode", !fit);
    fitButton.setAttribute("aria-pressed", String(fit));
    actualButton.setAttribute("aria-pressed", String(!fit));
  }

  fitButton.addEventListener("click", () => setScaleMode("fit"));
  actualButton.addEventListener("click", () => setScaleMode("actual"));
  fullscreenButton.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await stage.requestFullscreen();
      }
    } catch {
      showToast("Fullscreen is not available.");
    }
  });

  imeButton.addEventListener("click", () => {
    if (!canControl || !imeSupported || typeof imeEnabled !== "boolean" || imePending) return;
    imePending = true;
    refreshImeButton();
    if (!send({ type: "ime_set", enabled: !imeEnabled })) {
      imePending = false;
      refreshImeButton();
    }
  });

  clipboardSyncToggle.addEventListener("change", async () => {
    if (!clipboardSyncToggle.checked) {
      writeClipboardPreference(false);
      stopClipboardSyncRuntime();
      return;
    }
    const access = await requestClipboardAccess(true);
    if (!access.ok) {
      clipboardSyncToggle.checked = false;
      writeClipboardPreference(false);
      return;
    }
    writeClipboardPreference(true);
    startClipboardSync(access.text);
    showToast("Automatic clipboard sync enabled.");
  });

  clipboardButton.addEventListener("click", () => {
    clipboardDialog.showModal();
    send({ type: "clipboard_get" });
  });
  document.querySelector("#clipboard-read").addEventListener("click", () => {
    send({ type: "clipboard_get" });
  });
  document.querySelector("#clipboard-write").addEventListener("click", () => {
    sendClipboardSet(clipboardText.value, "manual");
  });
  document.querySelector("#clipboard-paste-local").addEventListener("click", async () => {
    try {
      clipboardText.value = await navigator.clipboard.readText();
    } catch {
      showToast("Browser clipboard access was denied.");
    }
  });
  document.querySelector("#clipboard-copy-local").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(clipboardText.value);
      publishClientClipboard(clipboardText.value);
      showToast("Copied to the browser clipboard.");
    } catch {
      showToast("Browser clipboard access was denied.");
    }
  });

  window.setInterval(() => send({ type: "ping" }), 15000);
  clipboardSyncToggle.checked = readClipboardPreference();
  connect();
})();
