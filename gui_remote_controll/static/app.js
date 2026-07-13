(() => {
  "use strict";

  const stage = document.querySelector("#desktop-stage");
  const screen = document.querySelector("#screen");
  const inputSink = document.querySelector("#input-sink");
  const connectionState = document.querySelector("#connection-state");
  const stageMessage = document.querySelector("#stage-message");
  const stageTitle = document.querySelector("#stage-title");
  const stageDetail = document.querySelector("#stage-detail");
  const screenInfo = document.querySelector("#screen-info");
  const modeInfo = document.querySelector("#mode-info");
  const monitorSelect = document.querySelector("#monitor-select");
  const fitButton = document.querySelector("#fit-button");
  const actualButton = document.querySelector("#actual-button");
  const fullscreenButton = document.querySelector("#fullscreen-button");
  const clipboardButton = document.querySelector("#clipboard-button");
  const clipboardDialog = document.querySelector("#clipboard-dialog");
  const clipboardText = document.querySelector("#clipboard-text");
  const toast = document.querySelector("#toast");

  let socket = null;
  let reconnectTimer = null;
  let reconnectDelay = 500;
  let currentFrameUrl = null;
  let viewOnly = false;
  let clipboardEnabled = false;
  let composing = false;
  let pendingPointer = null;
  let pointerFrame = null;
  let lastPointer = { x: 0.5, y: 0.5 };
  let toastTimer = null;
  const heldKeys = new Map();
  const heldButtons = new Set();

  function setConnection(label, state) {
    connectionState.textContent = label;
    connectionState.dataset.state = state;
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

    socket.addEventListener("close", (event) => {
      releaseLocalState(false);
      monitorSelect.disabled = true;
      clipboardButton.disabled = true;
      if (event.code === 4401) {
        window.location.assign("/auth");
        return;
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
        modeInfo.textContent = viewOnly ? "View only" : "Control enabled";
        clipboardButton.disabled = !clipboardEnabled;
        populateScreens(message.screens, message.monitor);
        break;
      case "screen":
        screenInfo.textContent = `${message.name} · ${message.width} × ${message.height}`;
        break;
      case "clipboard":
        clipboardText.value = message.text || "";
        break;
      case "clipboard_saved":
        showToast("Remote clipboard updated.");
        break;
      case "error":
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
    if (!viewOnly) {
      inputSink.focus({ preventScroll: true });
    }
  }

  screen.addEventListener("pointermove", (event) => {
    if (viewOnly) return;
    const point = pointerCoordinates(event);
    if (!point) return;
    pendingPointer = { type: "pointer", event: "move", ...point };
    if (pointerFrame === null) {
      pointerFrame = window.requestAnimationFrame(() => {
        if (pendingPointer) send(pendingPointer);
        pendingPointer = null;
        pointerFrame = null;
      });
    }
  });

  screen.addEventListener("pointerdown", (event) => {
    if (viewOnly) return;
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
    if (viewOnly) return;
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
    if (viewOnly) return;
    event.preventDefault();
    const divisor = event.deltaMode === WheelEvent.DOM_DELTA_PIXEL ? 100 : 3;
    send({
      type: "wheel",
      dx: Math.max(-20, Math.min(20, event.deltaX / divisor)),
      dy: Math.max(-20, Math.min(20, event.deltaY / divisor)),
    });
  }, { passive: false });

  inputSink.addEventListener("keydown", (event) => {
    if (viewOnly || event.isComposing || event.key === "Process" || event.key === "Dead") return;
    const pasteShortcut = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    if (pasteShortcut) return;
    const printable = event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
    if (printable) return;
    event.preventDefault();
    heldKeys.set(event.code || event.key, event.key);
    send({ type: "key", event: "down", key: event.key, code: event.code, repeat: event.repeat });
  });

  inputSink.addEventListener("keyup", (event) => {
    if (viewOnly || event.isComposing || event.key === "Process" || event.key === "Dead") return;
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
    const text = event.clipboardData?.getData("text/plain") || "";
    if (text) send({ type: "text", text });
  });

  function flushTextInput() {
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
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) releaseLocalState(true);
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

  clipboardButton.addEventListener("click", () => {
    clipboardDialog.showModal();
    send({ type: "clipboard_get" });
  });
  document.querySelector("#clipboard-read").addEventListener("click", () => {
    send({ type: "clipboard_get" });
  });
  document.querySelector("#clipboard-write").addEventListener("click", () => {
    send({ type: "clipboard_set", text: clipboardText.value });
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
      showToast("Copied to the browser clipboard.");
    } catch {
      showToast("Browser clipboard access was denied.");
    }
  });

  window.setInterval(() => send({ type: "ping" }), 15000);
  connect();
})();
