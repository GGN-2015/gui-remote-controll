from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gui_remote_controll.app import create_app
from gui_remote_controll.config import Settings
from gui_remote_controll.desktop import Frame, Screen
from gui_remote_controll.ime import ImeState


@dataclass
class FakeBackend:
    settings: Settings
    initialized: bool = False
    events: list[dict[str, object]] = field(default_factory=list)
    local_input_monitoring: bool = True
    local_input_monitoring_detail: str = "Test input monitor"
    local_input_callback: object | None = None
    ime_enabled: bool = True

    def set_local_input_callback(self, callback: object) -> None:
        self.local_input_callback = callback

    def initialize(self) -> None:
        self.initialized = True

    def list_screens(self) -> tuple[Screen, ...]:
        return (Screen(0, 0, 0, 800, 600, "All displays"), Screen(1, 0, 0, 800, 600, "Display 1"))

    def capture(self, monitor_index: int) -> Frame:
        screen = self.list_screens()[monitor_index]
        return Frame(b"\xff\xd8fake-jpeg\xff\xd9", screen)

    def execute(self, message: dict[str, object], screen: Screen) -> None:
        self.events.append(message)

    def release_inputs(self, keys: set[str], buttons: set[str]) -> None:
        return None

    def clipboard_get(self) -> str:
        return "remote text"

    def clipboard_set(self, text: str) -> None:
        return None

    def ime_status(self) -> ImeState:
        return ImeState(True, self.ime_enabled, "Test IME")

    def ime_set(self, enabled: bool) -> ImeState:
        self.ime_enabled = enabled
        self.events.append({"type": "ime_set", "enabled": enabled})
        return self.ime_status()

    def shutdown(self) -> None:
        return None


def test_health_and_security_headers() -> None:
    with TestClient(create_app(backend_factory=FakeBackend)) as client:
        response = client.get("/healthz")
        assert response.json()["status"] == "ok"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_control_ui_contains_automatic_clipboard_sync() -> None:
    with TestClient(create_app(backend_factory=FakeBackend)) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
    assert 'id="clipboard-sync-toggle"' in page.text
    assert "requestClipboardAccess" in script.text
    assert "clipboardchange" in script.text
    assert "knownDigest" in script.text
    assert 'id="control-permission"' in page.text
    assert 'id="ime-button"' in page.text
    assert "/static/app.js?v=0.1.3" in page.text
    assert "{{APP_" not in page.text


def test_custom_title_is_rendered_as_text() -> None:
    settings = Settings(title='<Lab & "desk">', pin="2468")
    with TestClient(create_app(settings, backend_factory=FakeBackend)) as client:
        auth_page = client.get("/auth")
        client.post("/auth", data={"pin": "2468"})
        control_page = client.get("/")
    assert "&lt;Lab &amp; &quot;desk&quot;&gt;" in auth_page.text
    assert "&lt;Lab &amp; &quot;desk&quot;&gt;" in control_page.text
    assert '<Lab & "desk">' not in control_page.text


def test_pin_login_sets_cookie_and_unlocks_home() -> None:
    app = create_app(Settings(pin="2468"), backend_factory=FakeBackend)
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/").status_code == 303
        failed = client.post("/auth?next=%2F", data={"pin": "0000"})
        assert "error=invalid" in failed.headers["location"]
        passed = client.post("/auth?next=%2F", data={"pin": "2468"})
        assert passed.status_code == 303
        assert "HttpOnly" in passed.headers["set-cookie"]
        assert client.get("/").status_code == 200


def test_websocket_sends_hello_metadata_and_binary_frame() -> None:
    app = create_app(Settings(fps=30), backend_factory=FakeBackend)
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws") as websocket,
    ):
        hello = websocket.receive_json()
        screen = websocket.receive_json()
        frame = websocket.receive_bytes()
        websocket.send_json({"type": "clipboard_get"})
        clipboard = websocket.receive_json()
        websocket.send_json({"type": "clipboard_get", "knownDigest": clipboard["digest"]})
        unchanged = websocket.receive_json()
        websocket.send_json(
            {
                "type": "clipboard_set",
                "text": "client text",
                "requestId": "request-1",
            }
        )
        saved = websocket.receive_json()
    assert hello["type"] == "hello"
    assert hello["protocol"] == 1
    assert hello["title"] == "GUI Remote Controll"
    assert hello["control"]["state"] == "available"
    assert hello["ime"]["enabled"] is True
    assert len(hello["screens"]) == 2
    assert screen["type"] == "screen"
    assert frame.startswith(b"\xff\xd8")
    assert clipboard["text"] == "remote text"
    assert unchanged == {
        "type": "clipboard_unchanged",
        "digest": clipboard["digest"],
    }
    assert saved["type"] == "clipboard_saved"
    assert saved["requestId"] == "request-1"


def test_ime_button_message_changes_server_state() -> None:
    app = create_app(Settings(fps=30), backend_factory=FakeBackend)
    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_bytes()
        websocket.send_json({"type": "ime_set", "enabled": False})
        ime_state = websocket.receive_json()
    assert ime_state == {
        "type": "ime_state",
        "supported": True,
        "enabled": False,
        "detail": "Test IME",
    }


def test_view_only_reports_restricted_control() -> None:
    app = create_app(Settings(view_only=True), backend_factory=FakeBackend)
    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        hello = websocket.receive_json()
    assert hello["control"]["state"] == "restricted"
    assert hello["control"]["reason"] == "view_only"


def test_physical_input_temporarily_blocks_remote_control() -> None:
    backends: list[FakeBackend] = []

    def factory(settings: Settings) -> FakeBackend:
        backend = FakeBackend(settings)
        backends.append(backend)
        return backend

    app = create_app(Settings(fps=30), backend_factory=factory)
    app.state.control_arbiter.hold_seconds = 0.2
    with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_bytes()
        app.state.control_arbiter.record_local_input()
        blocked = websocket.receive_json()
        websocket.send_json({"type": "pointer", "event": "move", "x": 0.25, "y": 0.75})
        for _ in range(10):
            available = websocket.receive_json()
            if available.get("state") == "available":
                break
    assert blocked["type"] == "control_state"
    assert blocked["state"] == "local_active"
    assert available["type"] == "control_state"
    assert available["state"] == "available"
    assert backends[0].events == []


def test_cross_origin_websocket_is_rejected() -> None:
    with (
        TestClient(create_app(backend_factory=FakeBackend)) as client,
        pytest.raises(WebSocketDisconnect) as raised,
        client.websocket_connect("/ws", headers={"origin": "https://attacker.invalid"}),
    ):
        pass
    assert raised.value.code == 4403
