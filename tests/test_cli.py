from __future__ import annotations

import pytest

from gui_remote_controll import __main__ as cli


def test_parser_matches_documented_defaults() -> None:
    args = cli.build_parser().parse_args(["--no-elevate"])
    settings = cli.settings_from_args(args)
    assert settings.title == "GUI Remote Controll"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.fps == 10
    assert settings.monitor == 1
    assert settings.clipboard_enabled


def test_custom_client_title() -> None:
    args = cli.build_parser().parse_args(["--title", "Lab workstation", "--no-elevate"])
    assert cli.settings_from_args(args).title == "Lab workstation"


def test_empty_client_title_is_rejected() -> None:
    args = cli.build_parser().parse_args(["--title", "   ", "--no-elevate"])
    with pytest.raises(ValueError, match="title"):
        cli.settings_from_args(args)


def test_invalid_frame_rate_is_rejected() -> None:
    args = cli.build_parser().parse_args(["--fps", "0", "--no-elevate"])
    with pytest.raises(ValueError, match="fps"):
        cli.settings_from_args(args)


def test_default_start_performs_one_elevation_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_launch(arguments: list[str]) -> int:
        seen.append(arguments)
        return 7

    monkeypatch.setattr(cli, "_launch_elevated", fake_launch)
    with pytest.raises(SystemExit) as raised:
        cli.main(["--host", "127.0.0.1"])
    assert raised.value.code == 7
    assert seen == [["--host", "127.0.0.1"]]


def test_attempt_marker_prevents_recursive_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[object] = []
    served: list[object] = []
    monkeypatch.setattr(cli, "_launch_elevated", lambda arguments: launched.append(arguments))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append((app, kwargs)))

    cli.main(["--host", "127.0.0.1", "--elevation-attempted"])

    assert not launched
    assert len(served) == 1
