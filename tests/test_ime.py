from __future__ import annotations

import subprocess

import pytest

from gui_remote_controll import ime


class FakeWindowsUser32:
    def __init__(self, state: dict[str, object]) -> None:
        self.state = state
        self.set_messages: list[bool] = []
        self.layout_messages: list[int] = []

    def GetForegroundWindow(self) -> int:
        return 100

    def GetWindowThreadProcessId(self, hwnd: int, process_id: object) -> int:
        return 10

    def GetKeyboardLayout(self, thread_id: int) -> int:
        return int(self.state["layout"])

    def GetKeyboardLayoutList(self, count: int, layouts: object) -> int:
        installed = self.state["layouts"]
        if count == 0:
            return len(installed)
        for index, layout in enumerate(installed[:count]):
            layouts[index] = layout
        return min(count, len(installed))

    def PostMessageW(self, hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        self.layout_messages.append(lparam)
        self.state["layout"] = lparam
        return True

    def SendMessageW(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if wparam == 0x0005:
            return int(bool(self.state["open"]))
        if wparam == 0x0006:
            self.set_messages.append(bool(lparam))
            if self.state.get("window_set_works", True):
                self.state["open"] = bool(lparam)
            return 0
        raise AssertionError(wparam)


class FakeWindowsImm32:
    def __init__(self, state: dict[str, object], ime_layout: int) -> None:
        self.state = state
        self.ime_layout = ime_layout
        self.context_set_calls: list[bool] = []
        self.hotkeys: list[int] = []

    def ImmIsIME(self, layout: int) -> bool:
        return layout == self.ime_layout

    def ImmGetContext(self, hwnd: int) -> int:
        return 300

    def ImmGetOpenStatus(self, context: int) -> bool:
        return True

    def ImmSetOpenStatus(self, context: int, enabled: bool) -> bool:
        self.context_set_calls.append(enabled)
        return True

    def ImmSimulateHotKey(self, hwnd: int, hotkey: int) -> bool:
        self.hotkeys.append(hotkey)
        self.state["open"] = not bool(self.state["open"])
        return False

    def ImmReleaseContext(self, hwnd: int, context: int) -> bool:
        return True

    def ImmGetDefaultIMEWnd(self, hwnd: int) -> int:
        return 200


def completed(arguments: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")


def test_windows_always_updates_default_ime_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ime_layout = 0x08040804
    state: dict[str, object] = {
        "layout": ime_layout,
        "layouts": [ime_layout],
        "open": True,
    }
    user32 = FakeWindowsUser32(state)
    imm32 = FakeWindowsImm32(state, ime_layout)
    monkeypatch.setattr(ime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ime, "_windows_libraries", lambda: (user32, imm32))
    controller = ime.ImeController()

    assert controller.set_enabled(False).enabled is False
    assert controller.set_enabled(True).enabled is True
    assert imm32.context_set_calls == [False, True]
    assert user32.set_messages == [False, True]


def test_windows_enable_restores_an_installed_ime_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_layout = 0x04090409
    ime_layout = 0x08040804
    state: dict[str, object] = {
        "layout": direct_layout,
        "layouts": [direct_layout, ime_layout],
        "open": False,
    }
    user32 = FakeWindowsUser32(state)
    imm32 = FakeWindowsImm32(state, ime_layout)
    monkeypatch.setattr(ime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ime, "_windows_libraries", lambda: (user32, imm32))

    enabled = ime.ImeController().set_enabled(True)

    assert enabled.enabled is True
    assert state["layout"] == ime_layout
    assert user32.layout_messages == [ime_layout]
    assert user32.set_messages == [True]


def test_windows_enable_falls_back_to_language_ime_hotkey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ime_layout = 0x08040804
    state: dict[str, object] = {
        "layout": ime_layout,
        "layouts": [ime_layout],
        "open": False,
        "window_set_works": False,
    }
    user32 = FakeWindowsUser32(state)
    imm32 = FakeWindowsImm32(state, ime_layout)
    monkeypatch.setattr(ime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ime, "_windows_libraries", lambda: (user32, imm32))

    enabled = ime.ImeController().set_enabled(True)

    assert enabled.enabled is True
    assert user32.set_messages == [True]
    assert imm32.hotkeys == [0x10]


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
