from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    """Runtime settings for one server process."""

    host: str = "0.0.0.0"
    port: int = 8000
    pin: str | None = None
    fps: int = 10
    jpeg_quality: int = 80
    monitor: int = 1
    view_only: bool = False
    clipboard_enabled: bool = True
    capture_cursor: bool = True
    max_clients: int = 4
    max_clipboard_chars: int = 1_000_000
    max_message_bytes: int = 1_100_000
    auth_cookie_name: str = "gui_remote_auth"
    auth_cookie_max_age: int = 60 * 60 * 24 * 7
    login_attempts: int = 5
    login_window_seconds: int = 60
    trusted_origins: tuple[str, ...] = ()
    tls_enabled: bool = False
    title: str = "GUI Remote Controll"
    desktop_uid: int | None = None
    desktop_gid: int | None = None
    desktop_user: str | None = None
    desktop_home: str | None = None

    @property
    def frame_interval(self) -> float:
        return 1.0 / self.fps

    def validate(self) -> None:
        if not self.title.strip():
            raise ValueError("title must not be empty")
        if len(self.title) > 200:
            raise ValueError("title must not exceed 200 characters")
        if not self.host:
            raise ValueError("host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not 1 <= self.fps <= 30:
            raise ValueError("fps must be between 1 and 30")
        if not 20 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg quality must be between 20 and 95")
        if self.monitor < 0:
            raise ValueError("monitor must be zero or greater")
        if not 1 <= self.max_clients <= 32:
            raise ValueError("max clients must be between 1 and 32")
        if not 1 <= self.max_clipboard_chars <= 10_000_000:
            raise ValueError("max clipboard chars must be between 1 and 10000000")
        if self.pin is not None and not self.pin:
            raise ValueError("pin must not be empty")
        if self.desktop_uid is not None and self.desktop_uid < 0:
            raise ValueError("desktop uid must be zero or greater")
        if self.desktop_gid is not None and self.desktop_gid < 0:
            raise ValueError("desktop gid must be zero or greater")
