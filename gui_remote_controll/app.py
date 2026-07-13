from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import platform
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.websockets import WebSocketDisconnect

from . import __version__
from .auth import LoginLimiter, PinAuth
from .config import Settings
from .desktop import DesktopBackend, DesktopUnavailableError, Frame, Screen
from .ime import ImeState
from .protocol import ProtocolError, validate_client_message

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "app.css",
    "app.js",
    "auth.css",
    "auth.js",
}
LOCAL_INPUT_HOLD_SECONDS = 0.8
REMOTE_INPUT_TYPES = {"pointer", "wheel", "key", "text"}


class Backend(Protocol):
    local_input_monitoring: bool
    local_input_monitoring_detail: str

    def initialize(self) -> None: ...

    def set_local_input_callback(self, callback: Callable[[], None]) -> None: ...

    def list_screens(self) -> tuple[Screen, ...]: ...

    def capture(self, monitor_index: int) -> Frame: ...

    def execute(self, message: dict[str, Any], screen: Screen) -> None: ...

    def release_inputs(self, keys: set[str], buttons: set[str]) -> None: ...

    def clipboard_get(self) -> str: ...

    def clipboard_set(self, text: str) -> None: ...

    def ime_status(self) -> ImeState: ...

    def ime_set(self, enabled: bool) -> ImeState: ...

    def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ControlState:
    state: str
    reason: str
    detail: str

    def as_message(self) -> dict[str, str]:
        return {"state": self.state, "reason": self.reason, "detail": self.detail}


class ControlArbiter:
    """Give physical input a short exclusive lease over remote input injection."""

    def __init__(self, *, view_only: bool, hold_seconds: float = LOCAL_INPUT_HOLD_SECONDS) -> None:
        self.view_only = view_only
        self.hold_seconds = hold_seconds
        self._lock = threading.RLock()
        self._local_active_until = 0.0
        self._monitoring_available = False
        self._monitoring_detail = "Local input monitoring has not started."

    def record_local_input(self) -> None:
        with self._lock:
            self._local_active_until = max(
                self._local_active_until,
                time.monotonic() + self.hold_seconds,
            )

    def set_monitoring(self, available: bool, detail: str) -> None:
        with self._lock:
            self._monitoring_available = available
            self._monitoring_detail = detail

    def snapshot(self) -> ControlState:
        with self._lock:
            return self._snapshot_locked()

    def execute_remote(self, operation: Callable[[], None]) -> bool:
        with self._lock:
            if self._snapshot_locked().state != "available":
                return False
            operation()
            return True

    def _snapshot_locked(self) -> ControlState:
        if self.view_only:
            return ControlState("restricted", "view_only", "The server is in view-only mode.")
        if not self._monitoring_available:
            return ControlState("restricted", "monitor_unavailable", self._monitoring_detail)
        if time.monotonic() < self._local_active_until:
            return ControlState(
                "local_active",
                "local_input",
                "Physical input on the server has priority.",
            )
        return ControlState("available", "remote_allowed", "Remote input is allowed.")


class BackendManager:
    def __init__(
        self,
        settings: Settings,
        factory: Callable[[Settings], Backend],
        arbiter: ControlArbiter,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.arbiter = arbiter
        self._backend: Backend | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> Backend:
        async with self._lock:
            if self._backend is None:
                backend = self.factory(self.settings)
                callback_setter = getattr(backend, "set_local_input_callback", None)
                if callable(callback_setter):
                    callback_setter(self.arbiter.record_local_input)
                await asyncio.to_thread(backend.initialize)
                self._backend = backend
                self.refresh_monitoring(backend)
            return self._backend

    def refresh_monitoring(self, backend: Backend) -> None:
        available = bool(getattr(backend, "local_input_monitoring", False))
        detail = str(
            getattr(
                backend,
                "local_input_monitoring_detail",
                "The desktop backend cannot monitor physical input.",
            )
        )
        self.arbiter.set_monitoring(available, detail)

    async def close(self) -> None:
        if self._backend is None:
            return
        shutdown = getattr(self._backend, "shutdown", None)
        if callable(shutdown):
            await asyncio.to_thread(shutdown)


class ConnectionGate:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.active = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> bool:
        async with self._lock:
            if self.active >= self.maximum:
                return False
            self.active += 1
            return True

    async def leave(self) -> None:
        async with self._lock:
            self.active = max(0, self.active - 1)


@dataclass(slots=True)
class ClientState:
    screens: tuple[Screen, ...]
    screen: Screen
    keys: set[str] = field(default_factory=set)
    buttons: set[str] = field(default_factory=set)


def create_app(
    settings: Settings | None = None,
    *,
    backend_factory: Callable[[Settings], Backend] = DesktopBackend,
) -> FastAPI:
    runtime = settings or Settings()
    runtime.validate()
    auth = PinAuth(runtime)
    limiter = LoginLimiter(runtime.login_attempts, runtime.login_window_seconds)
    arbiter = ControlArbiter(view_only=runtime.view_only)
    backend_manager = BackendManager(runtime, backend_factory, arbiter)
    gate = ConnectionGate(runtime.max_clients)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        yield
        await backend_manager.close()

    app = FastAPI(
        title=runtime.title,
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = runtime
    app.state.auth = auth
    app.state.backend_manager = backend_manager
    app.state.connection_gate = gate
    app.state.control_arbiter = arbiter

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; img-src 'self' blob: data:; "
            "connect-src 'self' ws: wss:; script-src 'self'; style-src 'self'"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path in {"/", "/auth"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/")
    async def index(request: Request) -> Response:
        if not auth.is_allowed(request):
            return RedirectResponse("/auth?next=%2F", status_code=303)
        return _html_page("index.html", runtime.title)

    @app.get("/auth")
    async def auth_page(request: Request) -> Response:
        if not auth.enabled or auth.is_allowed(request):
            return RedirectResponse("/", status_code=303)
        return _html_page("auth.html", runtime.title)

    @app.post("/auth")
    async def authenticate(request: Request) -> Response:
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        client_key = request.client.host if request.client else "unknown"
        next_url = _safe_next_url(request.query_params.get("next", "/"))
        if not limiter.is_allowed(client_key):
            query = urlencode({"error": "rate", "next": next_url})
            return RedirectResponse(f"/auth?{query}", status_code=303)
        body = await request.body()
        if len(body) > 4096:
            return Response(status_code=413)
        fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        pin = fields.get("pin", [""])[0]
        if not auth.verify_pin(pin):
            limiter.record_failure(client_key)
            query = urlencode({"error": "invalid", "next": next_url})
            return RedirectResponse(f"/auth?{query}", status_code=303)
        limiter.clear(client_key)
        response = RedirectResponse(next_url, status_code=303)
        auth.set_cookie(response)
        return response

    @app.post("/logout")
    async def logout() -> Response:
        response = RedirectResponse("/auth", status_code=303)
        auth.clear_cookie(response)
        return response

    @app.get("/api/status")
    async def status(request: Request) -> Response:
        if not auth.is_allowed(request):
            return JSONResponse({"detail": "PIN required."}, status_code=401)
        return JSONResponse(
            {
                "version": __version__,
                "title": runtime.title,
                "platform": platform.system(),
                "viewOnly": runtime.view_only,
                "clipboard": runtime.clipboard_enabled,
                "control": arbiter.snapshot().as_message(),
                "clients": gate.active,
                "maxClients": runtime.max_clients,
            }
        )

    @app.get("/static/{filename}")
    async def static_file(filename: str) -> Response:
        if filename not in STATIC_FILES:
            return Response(status_code=404)
        return FileResponse(
            STATIC_DIR / filename,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.websocket("/ws")
    async def remote_socket(websocket: WebSocket) -> None:
        if not auth.is_allowed(websocket):
            await websocket.close(code=4401, reason="PIN required")
            return
        if not _origin_allowed(websocket, runtime.trusted_origins):
            await websocket.close(code=4403, reason="Origin not allowed")
            return
        if not await gate.enter():
            await websocket.close(code=4429, reason="Too many clients")
            return

        await websocket.accept()
        backend: Backend | None = None
        state: ClientState | None = None
        try:
            try:
                backend = await backend_manager.get()
                screens = await asyncio.to_thread(backend.list_screens)
                screen = _select_screen(screens, runtime.monitor)
                state = ClientState(screens=screens, screen=screen)
                ime_state = await asyncio.to_thread(backend.ime_status)
            except DesktopUnavailableError as exc:
                await websocket.send_json({"type": "fatal", "message": str(exc)})
                await websocket.close(code=4500, reason="Desktop unavailable")
                return

            send_lock = asyncio.Lock()
            initial_control_state = arbiter.snapshot()
            await websocket.send_json(
                {
                    "type": "hello",
                    "protocol": 1,
                    "title": runtime.title,
                    "platform": platform.system(),
                    "viewOnly": runtime.view_only,
                    "clipboard": runtime.clipboard_enabled,
                    "control": initial_control_state.as_message(),
                    "ime": ime_state.as_message(),
                    "screens": [item.as_message() for item in screens],
                    "monitor": screen.index,
                }
            )
            stream_task = asyncio.create_task(
                _stream_frames(websocket, backend, state, runtime, send_lock)
            )
            receive_task = asyncio.create_task(
                _receive_messages(websocket, backend, state, runtime, send_lock, arbiter)
            )
            control_task = asyncio.create_task(
                _stream_control_state(
                    websocket,
                    backend,
                    backend_manager,
                    state,
                    send_lock,
                    arbiter,
                    initial_control_state,
                )
            )
            done, pending = await asyncio.wait(
                {stream_task, receive_task, control_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()
        except WebSocketDisconnect:
            pass
        finally:
            if backend is not None and state is not None:
                await _release_client_inputs(backend, state)
            await gate.leave()
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    return app


async def _stream_frames(
    websocket: WebSocket,
    backend: Backend,
    state: ClientState,
    settings: Settings,
    send_lock: asyncio.Lock,
) -> None:
    last_digest = b""
    last_monitor = -1
    while True:
        started = asyncio.get_running_loop().time()
        try:
            frame = await asyncio.to_thread(backend.capture, state.screen.index)
        except DesktopUnavailableError as exc:
            async with send_lock:
                await websocket.send_json({"type": "fatal", "message": str(exc)})
            return
        digest = hashlib.blake2b(frame.data, digest_size=8).digest()
        async with send_lock:
            if frame.screen.index != last_monitor:
                await websocket.send_json({"type": "screen", **frame.screen.as_message()})
                last_monitor = frame.screen.index
            if digest != last_digest:
                await websocket.send_bytes(frame.data)
                last_digest = digest
        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(0.001, settings.frame_interval - elapsed))


async def _stream_control_state(
    websocket: WebSocket,
    backend: Backend,
    backend_manager: BackendManager,
    state: ClientState,
    send_lock: asyncio.Lock,
    arbiter: ControlArbiter,
    initial_state: ControlState,
) -> None:
    previous = initial_state
    while True:
        backend_manager.refresh_monitoring(backend)
        current = arbiter.snapshot()
        if current != previous:
            if current.state != "available":
                await _release_client_inputs(backend, state)
            async with send_lock:
                await websocket.send_json({"type": "control_state", **current.as_message()})
            previous = current
        await asyncio.sleep(0.05 if current.state == "local_active" else 0.15)


async def _receive_messages(
    websocket: WebSocket,
    backend: Backend,
    state: ClientState,
    settings: Settings,
    send_lock: asyncio.Lock,
    arbiter: ControlArbiter,
) -> None:
    while True:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > settings.max_message_bytes:
            await _send_error(websocket, send_lock, "Message is too large.")
            continue
        try:
            message = validate_client_message(
                json.loads(raw), max_text_chars=settings.max_clipboard_chars
            )
        except (json.JSONDecodeError, ProtocolError) as exc:
            await _send_error(websocket, send_lock, str(exc))
            continue

        message_type = message["type"]
        if message_type == "ping":
            async with send_lock:
                await websocket.send_json({"type": "pong"})
            continue
        if message_type == "monitor":
            selected = next(
                (item for item in state.screens if item.index == message["index"]),
                None,
            )
            if selected is None:
                await _send_error(websocket, send_lock, "The selected monitor is unavailable.")
            else:
                state.screen = selected
            continue
        if message_type == "clipboard_get":
            if not settings.clipboard_enabled:
                await _send_error(websocket, send_lock, "Clipboard synchronization is disabled.")
                continue
            try:
                text = await asyncio.to_thread(backend.clipboard_get)
                digest = _clipboard_digest(text)
                async with send_lock:
                    if message["knownDigest"] == digest:
                        await websocket.send_json({"type": "clipboard_unchanged", "digest": digest})
                    else:
                        await websocket.send_json(
                            {"type": "clipboard", "text": text, "digest": digest}
                        )
            except DesktopUnavailableError as exc:
                await _send_error(websocket, send_lock, str(exc))
            continue
        if message_type == "clipboard_set":
            if not settings.clipboard_enabled:
                await _send_error(websocket, send_lock, "Clipboard synchronization is disabled.")
                continue
            try:
                await asyncio.to_thread(backend.clipboard_set, message["text"])
                async with send_lock:
                    await websocket.send_json(
                        {
                            "type": "clipboard_saved",
                            "digest": _clipboard_digest(message["text"]),
                            "requestId": message["requestId"],
                        }
                    )
            except DesktopUnavailableError as exc:
                await _send_error(
                    websocket,
                    send_lock,
                    str(exc),
                    request_id=message["requestId"],
                )
            continue
        if message_type == "ime_set":
            if arbiter.snapshot().state != "available":
                await _send_control_state(websocket, send_lock, arbiter.snapshot())
                continue
            try:
                ime_state = await asyncio.to_thread(backend.ime_set, message["enabled"])
                async with send_lock:
                    await websocket.send_json({"type": "ime_state", **ime_state.as_message()})
            except DesktopUnavailableError as exc:
                await _send_error(websocket, send_lock, str(exc))
            continue
        if message_type not in REMOTE_INPUT_TYPES:
            continue
        if message_type == "key" and message["event"] == "down" and message["repeat"]:
            continue
        tracked_key = message_type == "key" and message["event"] == "down"
        tracked_button = message_type == "pointer" and message["event"] == "down"
        if tracked_key:
            state.keys.add(message["key"])
        elif tracked_button:
            state.buttons.add(message["button"])
        try:
            allowed = await asyncio.to_thread(
                _execute_remote_message,
                arbiter,
                backend,
                message,
                state.screen,
            )
        except DesktopUnavailableError as exc:
            if tracked_key:
                state.keys.discard(message["key"])
            elif tracked_button:
                state.buttons.discard(message["button"])
            await _send_error(websocket, send_lock, str(exc))
            continue
        if not allowed:
            if tracked_key:
                state.keys.discard(message["key"])
            elif tracked_button:
                state.buttons.discard(message["button"])
            await _send_control_state(websocket, send_lock, arbiter.snapshot())
            continue
        if message_type == "key":
            if message["event"] == "up":
                state.keys.discard(message["key"])
        elif message_type == "pointer" and message["event"] == "up":
            state.buttons.discard(message["button"])


def _execute_remote_message(
    arbiter: ControlArbiter,
    backend: Backend,
    message: dict[str, Any],
    screen: Screen,
) -> bool:
    return arbiter.execute_remote(lambda: backend.execute(message, screen))


async def _release_client_inputs(backend: Backend, state: ClientState) -> None:
    keys = set(state.keys)
    buttons = set(state.buttons)
    state.keys.clear()
    state.buttons.clear()
    if keys or buttons:
        await asyncio.to_thread(backend.release_inputs, keys, buttons)


async def _send_control_state(
    websocket: WebSocket,
    lock: asyncio.Lock,
    state: ControlState,
) -> None:
    async with lock:
        await websocket.send_json({"type": "control_state", **state.as_message()})


async def _send_error(
    websocket: WebSocket,
    lock: asyncio.Lock,
    message: str,
    *,
    request_id: str = "",
) -> None:
    payload = {"type": "error", "message": message}
    if request_id:
        payload["requestId"] = request_id
    async with lock:
        await websocket.send_json(payload)


def _clipboard_digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _html_page(filename: str, title: str) -> HTMLResponse:
    template = (STATIC_DIR / filename).read_text(encoding="utf-8")
    content = template.replace("{{APP_TITLE}}", html.escape(title))
    return HTMLResponse(content.replace("{{APP_VERSION}}", __version__))


def _select_screen(screens: tuple[Screen, ...], preferred: int) -> Screen:
    selected = next((item for item in screens if item.index == preferred), None)
    if selected is not None:
        return selected
    physical = next((item for item in screens if item.index > 0), None)
    if physical is not None:
        return physical
    if screens:
        return screens[0]
    raise DesktopUnavailableError("No display is available.")


def _safe_next_url(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value[:2048]


def _origin_allowed(websocket: WebSocket, trusted_origins: tuple[str, ...]) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc.lower() == websocket.headers.get("host", "").lower():
        return True
    normalized = origin.rstrip("/").lower()
    return normalized in {item.rstrip("/").lower() for item in trusted_origins}


app = create_app()
