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
    engines = "xkb:us::eng\nlibpinyin\n"

    monkeypatch.setattr(ime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ime.shutil,
        "which",
        lambda command: "/usr/bin/ibus" if command == "ibus" else None,
    )

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments == ["ibus", "engine"]:
            return completed(arguments, f"{current['engine']}\n")
        if arguments == ["ibus", "list-engine", "--name-only"]:
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


def test_ibus_prefers_configured_preload_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ime.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command == "gsettings" else None,
    )
    monkeypatch.setattr(
        ime,
        "_run_command",
        lambda arguments: completed(arguments, "['libchewing', 'xkb:gb::eng']\n"),
    )
    engines = ("xkb:us::eng", "libpinyin", "libchewing", "xkb:gb::eng")

    assert ime._find_ibus_engine(engines, want_ime=True, session=None) == "libchewing"
    assert ime._find_ibus_engine(engines, want_ime=False, session=None) == "xkb:gb::eng"


def test_macos_uses_source_type_and_ascii_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {"source": ime.MacInputSource("org.example.greek", "", "layout", False)}
    selected: list[tuple[bool, str | None, str | None]] = []

    monkeypatch.setattr(ime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ime, "_macos_current_source", lambda api: current["source"])

    def select_source(
        api: object,
        *,
        enabled: bool,
        preferred_source_id: str | None,
        preferred_bundle_id: str | None,
    ) -> None:
        selected.append((enabled, preferred_source_id, preferred_bundle_id))
        current["source"] = (
            ime.MacInputSource("org.example.pinyin", "org.example.ime", "input_mode", False)
            if enabled
            else ime.MacInputSource("com.apple.keylayout.US", "", "layout", True)
        )

    monkeypatch.setattr(ime, "_select_macos_source", select_source)
    controller = ime.ImeController(delegate_macos=False)
    controller._mac_api = {}

    assert controller.status().enabled is False
    current["source"] = ime.MacInputSource(
        "org.example.pinyin", "org.example.ime", "input_mode", False
    )
    assert controller.set_enabled(False).enabled is False
    assert controller.set_enabled(True).enabled is True
    assert selected == [
        (False, None, "org.example.ime"),
        (True, "org.example.pinyin", None),
    ]


def test_macos_ascii_input_mode_is_direct_input() -> None:
    source = ime.MacInputSource("org.example.ime.roman", "org.example.ime", "input_mode", True)
    assert source.ime_enabled is False


def test_platform_loader_value_error_becomes_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ime, "_load_macos_api", lambda: (_ for _ in ()).throw(ValueError("symbol")))

    state = ime.ImeController(delegate_macos=False).status()

    assert state.supported is False
    assert state.enabled is None
    assert state.detail == "symbol"


def test_elevated_linux_command_drops_to_desktop_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ime.DesktopSession(uid=1000, gid=1000, user="neko", home="/home/neko")
    monkeypatch.setattr(ime.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(ime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ime.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command in {"setpriv", "env"} else None,
    )
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")

    command = ime._desktop_session_command(["ibus", "engine"], session, macos_session=False)

    assert command[:7] == [
        "/usr/bin/setpriv",
        "--reuid",
        "1000",
        "--regid",
        "1000",
        "--init-groups",
        "--",
    ]
    assert "/usr/bin/env" in command
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in command
    assert command[-2:] == ["ibus", "engine"]


def test_elevated_macos_status_uses_desktop_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ime.DesktopSession(uid=501, gid=20, user="neko", home="/Users/neko")
    observed: list[tuple[list[str], ime.DesktopSession | None, bool]] = []
    monkeypatch.setattr(ime.os, "name", "posix")
    monkeypatch.setattr(ime.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(ime.platform, "system", lambda: "Darwin")

    def run(
        arguments: list[str],
        *,
        session: ime.DesktopSession | None = None,
        macos_session: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        observed.append((arguments, session, macos_session))
        return completed(
            arguments,
            '{"supported":true,"enabled":true,"detail":"macOS helper",'
            '"sourceId":"com.apple.inputmethod.SCIM.ITABC"}',
        )

    monkeypatch.setattr(ime, "_run_command", run)
    state = ime.ImeController(session).status()

    assert state == ime.ImeState(True, True, "macOS helper")
    assert observed[0][0][-2:] == ["gui_remote_controll._ime_helper", "status"]
    assert observed[0][1] == session
    assert observed[0][2] is True


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
