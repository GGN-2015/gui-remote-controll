from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from hashlib import sha256
from typing import Protocol

from starlette.responses import Response

from .config import Settings


class CookieSource(Protocol):
    cookies: dict[str, str]


class PinAuth:
    """Process-local PIN authentication using an HTTP-only signed cookie."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._secret = secrets.token_bytes(32)

    @property
    def enabled(self) -> bool:
        return self.settings.pin is not None

    def verify_pin(self, candidate: str) -> bool:
        expected = self.settings.pin or ""
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))

    def token(self) -> str:
        if not self.enabled:
            return ""
        return hmac.new(
            self._secret,
            (self.settings.pin or "").encode("utf-8"),
            sha256,
        ).hexdigest()

    def is_allowed(self, source: CookieSource) -> bool:
        if not self.enabled:
            return True
        supplied = source.cookies.get(self.settings.auth_cookie_name, "")
        return hmac.compare_digest(supplied, self.token())

    def set_cookie(self, response: Response) -> None:
        response.set_cookie(
            self.settings.auth_cookie_name,
            self.token(),
            max_age=self.settings.auth_cookie_max_age,
            httponly=True,
            secure=self.settings.tls_enabled,
            samesite="strict",
            path="/",
        )

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(self.settings.auth_cookie_name, path="/")


class LoginLimiter:
    """Small in-memory rolling-window limiter for failed PIN attempts."""

    def __init__(self, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_allowed(self, key: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        with self._lock:
            failures = self._failures[key]
            self._discard_expired(failures, timestamp)
            return len(failures) < self.attempts

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        timestamp = time.monotonic() if now is None else now
        with self._lock:
            failures = self._failures[key]
            self._discard_expired(failures, timestamp)
            failures.append(timestamp)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def _discard_expired(self, failures: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
