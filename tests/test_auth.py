from __future__ import annotations

from dataclasses import dataclass, field

from starlette.responses import Response

from gui_remote_controll.auth import LoginLimiter, PinAuth
from gui_remote_controll.config import Settings


@dataclass
class CookieSource:
    cookies: dict[str, str] = field(default_factory=dict)


def test_pin_auth_sets_http_only_strict_cookie() -> None:
    auth = PinAuth(Settings(pin="123456"))
    source = CookieSource()
    assert auth.enabled
    assert auth.verify_pin("123456")
    assert not auth.verify_pin("123457")
    assert not auth.is_allowed(source)

    response = Response()
    auth.set_cookie(response)
    cookie = response.headers["set-cookie"]
    source.cookies["gui_remote_auth"] = auth.token()
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert auth.is_allowed(source)


def test_auth_is_open_without_pin() -> None:
    auth = PinAuth(Settings())
    assert not auth.enabled
    assert auth.is_allowed(CookieSource())


def test_login_limiter_expires_fixed_window() -> None:
    limiter = LoginLimiter(attempts=2, window_seconds=10)
    assert limiter.is_allowed("client", now=0)
    limiter.record_failure("client", now=0)
    limiter.record_failure("client", now=1)
    assert not limiter.is_allowed("client", now=2)
    assert limiter.is_allowed("client", now=11)
