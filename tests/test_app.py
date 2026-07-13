from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gui_remote_controll.app import create_app
from gui_remote_controll.config import Settings
from gui_remote_controll.desktop import Frame, Screen


@dataclass
class FakeBackend:
    settings: Settings
    initialized: bool = False
    events: list[dict[str, object]] = field(default_factory=list)

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


def test_health_and_security_headers() -> None:
    with TestClient(create_app(backend_factory=FakeBackend)) as client:
        response = client.get("/healthz")
        assert response.json()["status"] == "ok"
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


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
    assert hello["type"] == "hello"
    assert hello["protocol"] == 1
    assert len(hello["screens"]) == 2
    assert screen["type"] == "screen"
    assert frame.startswith(b"\xff\xd8")


def test_cross_origin_websocket_is_rejected() -> None:
    with (
        TestClient(create_app(backend_factory=FakeBackend)) as client,
        pytest.raises(WebSocketDisconnect) as raised,
        client.websocket_connect("/ws", headers={"origin": "https://attacker.invalid"}),
    ):
        pass
    assert raised.value.code == 4403
