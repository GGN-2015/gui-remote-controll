from __future__ import annotations

import subprocess

import pytest

from gui_remote_controll import ime


def completed(arguments: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")


def test_fcitx_status_and_explicit_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"value": "2"}

    monkeypatch.setattr(ime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ime.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command == "fcitx5-remote" else None,
    )

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments[-1:] == ["-c"]:
            state["value"] = "1"
        elif arguments[-1:] == ["-o"]:
            state["value"] = "2"
        return completed(arguments, f"{state['value']}\n")

    monkeypatch.setattr(ime, "_run_command", run)
    controller = ime.ImeController()

    assert controller.status().enabled is True
    assert controller.set_enabled(False).enabled is False
    assert controller.set_enabled(True).enabled is True


def test_ibus_restores_engine_after_direct_input(monkeypatch: pytest.MonkeyPatch) -> None:
    current = {"engine": "libpinyin"}
    engines = "name: xkb:us::eng\nname: libpinyin\n"

    monkeypatch.setattr(ime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ime.shutil,
        "which",
        lambda command: "/usr/bin/ibus" if command == "ibus" else None,
    )

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments == ["ibus", "engine"]:
            return completed(arguments, f"{current['engine']}\n")
        if arguments == ["ibus", "list-engine"]:
            return completed(arguments, engines)
        if arguments[:2] == ["ibus", "engine"]:
            current["engine"] = arguments[2]
            return completed(arguments)
        raise AssertionError(arguments)

    monkeypatch.setattr(ime, "_run_command", run)
    controller = ime.ImeController()

    assert controller.set_enabled(False).enabled is False
    assert current["engine"] == "xkb:us::eng"
    assert controller.set_enabled(True).enabled is True
    assert current["engine"] == "libpinyin"


def test_inactive_fcitx_installation_falls_back_to_ibus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ime.shutil, "which", lambda command: f"/usr/bin/{command}")

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments == ["fcitx5-remote"] or arguments == ["fcitx-remote"]:
            return completed(arguments, "0\n")
        if arguments == ["ibus", "engine"]:
            return completed(arguments, "libpinyin\n")
        raise AssertionError(arguments)

    monkeypatch.setattr(ime, "_run_command", run)
    state = ime.ImeController().status()

    assert state.supported
    assert state.enabled is True
    assert state.detail == "IBus (libpinyin)"
