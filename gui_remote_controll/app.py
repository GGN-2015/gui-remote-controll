from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import platform
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.websockets import WebSocketDisconnect

from . import __version__
from .auth import LoginLimiter, PinAuth
from .config import Settings
from .desktop import DesktopBackend, DesktopUnavailableError, Frame, Screen
from .protocol import ProtocolError, validate_client_message

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "app.css",
    "app.js",
    "auth.css",
    "auth.js",
}


class Backend(Protocol):
    def initialize(self) -> None: ...

    def list_screens(self) -> tuple[Screen, ...]: ...

    def capture(self, monitor_index: int) -> Frame: ...

    def execute(self, message: dict[str, Any], screen: Screen) -> None: ...

    def release_inputs(self, keys: set[str], buttons: set[str]) -> None: ...

    def clipboard_get(self) -> str: ...

    def clipboard_set(self, text: str) -> None: ...


class BackendManager:
    def __init__(self, settings: Settings, factory: Callable[[Settings], Backend]) -> None:
        self.settings = settings
        self.factory = factory
        self._backend: Backend | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> Backend:
        async with self._lock:
            if self._backend is None:
                backend = self.factory(self.settings)
                await asyncio.to_thread(backend.initialize)
                self._backend = backend
            return self._backend


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
    backend_manager = BackendManager(runtime, backend_factory)
    gate = ConnectionGate(runtime.max_clients)

    app = FastAPI(
        title="GUI Remote Controll",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = runtime
    app.state.auth = auth
    app.state.backend_manager = backend_manager
    app.state.connection_gate = gate

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
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/auth")
    async def auth_page(request: Request) -> Response:
        if not auth.enabled or auth.is_allowed(request):
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC_DIR / "auth.html")

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
                "platform": platform.system(),
                "viewOnly": runtime.view_only,
                "clipboard": runtime.clipboard_enabled,
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
            except DesktopUnavailableError as exc:
                await websocket.send_json({"type": "fatal", "message": str(exc)})
                await websocket.close(code=4500, reason="Desktop unavailable")
                return

            send_lock = asyncio.Lock()
            await websocket.send_json(
                {
                    "type": "hello",
                    "protocol": 1,
                    "platform": platform.system(),
                    "viewOnly": runtime.view_only,
                    "clipboard": runtime.clipboard_enabled,
                    "screens": [item.as_message() for item in screens],
                    "monitor": screen.index,
                }
            )
            stream_task = asyncio.create_task(
                _stream_frames(websocket, backend, state, runtime, send_lock)
            )
            receive_task = asyncio.create_task(
                _receive_messages(websocket, backend, state, runtime, send_lock)
            )
            done, pending = await asyncio.wait(
                {stream_task, receive_task}, return_when=asyncio.FIRST_COMPLETED
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
                await asyncio.to_thread(backend.release_inputs, state.keys, state.buttons)
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


async def _receive_messages(
    websocket: WebSocket,
    backend: Backend,
    state: ClientState,
    settings: Settings,
    send_lock: asyncio.Lock,
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
                async with send_lock:
                    await websocket.send_json({"type": "clipboard", "text": text})
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
                    await websocket.send_json({"type": "clipboard_saved"})
            except DesktopUnavailableError as exc:
                await _send_error(websocket, send_lock, str(exc))
            continue
        if settings.view_only:
            continue
        if message_type == "key":
            if message["event"] == "down":
                state.keys.add(message["key"])
                if message["repeat"]:
                    continue
            else:
                state.keys.discard(message["key"])
        elif message_type == "pointer" and message["event"] != "move":
            if message["event"] == "down":
                state.buttons.add(message["button"])
            else:
                state.buttons.discard(message["button"])
        try:
            await asyncio.to_thread(backend.execute, message, state.screen)
        except DesktopUnavailableError as exc:
            await _send_error(websocket, send_lock, str(exc))


async def _send_error(websocket: WebSocket, lock: asyncio.Lock, message: str) -> None:
    async with lock:
        await websocket.send_json({"type": "error", "message": message})


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
