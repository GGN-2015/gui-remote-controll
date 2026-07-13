from __future__ import annotations

import ctypes
import ctypes.util
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any


class ImeControlError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImeState:
    supported: bool
    enabled: bool | None
    detail: str

    def as_message(self) -> dict[str, bool | str | None]:
        return asdict(self)


class ImeController:
    """Best-effort native input method controller for the interactive desktop."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._saved_windows_layout: int | None = None
        self._saved_ibus_engine: str | None = None
        self._saved_macos_source: int | None = None
        self._mac_api: dict[str, Any] | None = None

    def status(self) -> ImeState:
        with self._lock:
            system = platform.system()
            try:
                if system == "Windows":
                    return self._windows_status()
                if system == "Linux":
                    return self._linux_status()
                if system == "Darwin":
                    return self._macos_status()
            except (ImeControlError, OSError, subprocess.SubprocessError) as exc:
                return ImeState(False, None, str(exc))
            return ImeState(False, None, f"IME control is not supported on {system}.")

    def set_enabled(self, enabled: bool) -> ImeState:
        with self._lock:
            system = platform.system()
            if system == "Windows":
                self._windows_set(enabled)
            elif system == "Linux":
                self._linux_set(enabled)
            elif system == "Darwin":
                self._macos_set(enabled)
            else:
                raise ImeControlError(f"IME control is not supported on {system}.")
            attempts = 5 if system == "Windows" else 1
            for attempt in range(attempts):
                state = self.status()
                if state.supported and state.enabled is enabled:
                    return state
                if attempt + 1 < attempts:
                    time.sleep(0.05)
            raise ImeControlError(
                state.detail or "The operating system did not apply the requested IME state."
            )

    def close(self) -> None:
        with self._lock:
            if self._saved_macos_source is not None and self._mac_api is not None:
                self._mac_api["core_foundation"].CFRelease(self._saved_macos_source)
                self._saved_macos_source = None

    def _windows_status(self) -> ImeState:
        user32, imm32 = _windows_libraries()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ImeState(True, None, "No foreground window has an input context.")
        layout = _windows_foreground_layout(user32, hwnd)
        if layout and imm32.ImmIsIME(layout):
            self._saved_windows_layout = layout
        elif layout:
            return ImeState(True, False, "Windows direct-input layout")

        enabled = _windows_open_status(user32, imm32, hwnd)
        if enabled is not None:
            return ImeState(True, enabled, "Windows IMM32")

        context = imm32.ImmGetContext(hwnd)
        if context:
            try:
                enabled = bool(imm32.ImmGetOpenStatus(context))
            finally:
                imm32.ImmReleaseContext(hwnd, context)
            return ImeState(True, enabled, "Windows IMM32")
        return ImeState(True, None, "The foreground window has no accessible IME.")

    def _windows_set(self, enabled: bool) -> None:
        user32, imm32 = _windows_libraries()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise ImeControlError("No foreground window has an input context.")
        layout = _windows_foreground_layout(user32, hwnd)
        if layout and imm32.ImmIsIME(layout):
            self._saved_windows_layout = layout
        elif enabled:
            layouts = _windows_ime_layouts(user32, imm32)
            target = (
                self._saved_windows_layout
                if self._saved_windows_layout in layouts
                else next(iter(layouts), None)
            )
            if target is None:
                raise ImeControlError("Windows has no installed IME input layout to activate.")
            if not user32.PostMessageW(hwnd, 0x0050, 0x0001, target):
                raise ImeControlError("Windows rejected the IME input layout request.")
            deadline = time.monotonic() + 0.4
            while time.monotonic() < deadline:
                layout = _windows_foreground_layout(user32, hwnd)
                if layout and imm32.ImmIsIME(layout):
                    self._saved_windows_layout = layout
                    break
                time.sleep(0.02)

        context = imm32.ImmGetContext(hwnd)
        context_applied = False
        if context:
            try:
                context_applied = bool(imm32.ImmSetOpenStatus(context, enabled))
            finally:
                imm32.ImmReleaseContext(hwnd, context)
        ime_window = imm32.ImmGetDefaultIMEWnd(hwnd)
        if ime_window:
            user32.SendMessageW(ime_window, 0x0283, 0x0006, int(enabled))
        elif not context_applied:
            raise ImeControlError("The foreground window has no accessible IME.")

        open_status = _windows_open_status(user32, imm32, hwnd)
        if open_status is not None and open_status is not enabled:
            layout = _windows_foreground_layout(user32, hwnd)
            hotkey = _windows_toggle_hotkey(layout)
            if hotkey is not None:
                imm32.ImmSimulateHotKey(hwnd, hotkey)

    def _linux_status(self) -> ImeState:
        fcitx = _active_fcitx()
        if fcitx is not None:
            command, state = fcitx
            return ImeState(True, state == "2", command)
        if shutil.which("ibus"):
            engine = _run_command(["ibus", "engine"]).stdout.strip()
            if not engine:
                raise ImeControlError("IBus did not report an active engine.")
            return ImeState(True, not engine.startswith("xkb:"), f"IBus ({engine})")
        return ImeState(
            False,
            None,
            "Install or start Fcitx 4/5 or IBus in the current desktop session.",
        )

    def _linux_set(self, enabled: bool) -> None:
        fcitx = _active_fcitx()
        if fcitx is not None:
            command, _ = fcitx
            _run_command([command, "-o" if enabled else "-c"])
            return
        if not shutil.which("ibus"):
            raise ImeControlError(
                "Install or start Fcitx 4/5 or IBus in the current desktop session."
            )
        current = _run_command(["ibus", "engine"]).stdout.strip()
        if not current:
            raise ImeControlError("IBus did not report an active engine.")
        if enabled:
            target = self._saved_ibus_engine or _find_ibus_engine(want_ime=True)
            if target is None:
                raise ImeControlError("IBus has no enabled input method engine to restore.")
        else:
            if not current.startswith("xkb:"):
                self._saved_ibus_engine = current
            target = _find_ibus_engine(want_ime=False)
            if target is None:
                raise ImeControlError("IBus has no XKB engine available for direct input.")
        _run_command(["ibus", "engine", target])

    def _macos_status(self) -> ImeState:
        api = self._macos_api()
        source = api["carbon"].TISCopyCurrentKeyboardInputSource()
        if not source:
            raise ImeControlError("macOS did not report a current keyboard input source.")
        try:
            ascii_capable = _macos_property(api, source, "ascii")
        finally:
            api["core_foundation"].CFRelease(source)
        return ImeState(True, not ascii_capable, "macOS Text Input Source")

    def _macos_set(self, enabled: bool) -> None:
        api = self._macos_api()
        carbon = api["carbon"]
        core_foundation = api["core_foundation"]
        current = carbon.TISCopyCurrentKeyboardInputSource()
        if not current:
            raise ImeControlError("macOS did not report a current keyboard input source.")
        keep_current = False
        try:
            current_is_ascii = _macos_property(api, current, "ascii")
            if not enabled and not current_is_ascii:
                if self._saved_macos_source is not None:
                    core_foundation.CFRelease(self._saved_macos_source)
                self._saved_macos_source = current
                keep_current = True

            if enabled and self._saved_macos_source is not None:
                result = carbon.TISSelectInputSource(self._saved_macos_source)
            else:
                result = _select_macos_source(api, ascii_capable=not enabled)
            if result != 0:
                raise ImeControlError(f"macOS rejected the input source change ({result}).")
        finally:
            if not keep_current:
                core_foundation.CFRelease(current)

    def _macos_api(self) -> dict[str, Any]:
        if self._mac_api is None:
            self._mac_api = _load_macos_api()
        return self._mac_api


def _windows_libraries() -> tuple[Any, Any]:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    user32.GetKeyboardLayoutList.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = wintypes.LPARAM
    imm32.ImmGetContext.argtypes = [wintypes.HWND]
    imm32.ImmGetContext.restype = ctypes.c_void_p
    imm32.ImmReleaseContext.argtypes = [wintypes.HWND, ctypes.c_void_p]
    imm32.ImmReleaseContext.restype = wintypes.BOOL
    imm32.ImmGetOpenStatus.argtypes = [ctypes.c_void_p]
    imm32.ImmGetOpenStatus.restype = wintypes.BOOL
    imm32.ImmSetOpenStatus.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    imm32.ImmSetOpenStatus.restype = wintypes.BOOL
    imm32.ImmSimulateHotKey.argtypes = [wintypes.HWND, wintypes.DWORD]
    imm32.ImmSimulateHotKey.restype = wintypes.BOOL
    imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
    imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
    imm32.ImmIsIME.argtypes = [ctypes.c_void_p]
    imm32.ImmIsIME.restype = wintypes.BOOL
    return user32, imm32


def _windows_foreground_layout(user32: Any, hwnd: int) -> int | None:
    from ctypes import wintypes

    process_id = wintypes.DWORD()
    thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    if not thread_id:
        return None
    layout = user32.GetKeyboardLayout(thread_id)
    return int(layout) if layout else None


def _windows_ime_layouts(user32: Any, imm32: Any) -> tuple[int, ...]:
    count = int(user32.GetKeyboardLayoutList(0, None))
    if count <= 0:
        return ()
    layouts = (ctypes.c_void_p * count)()
    copied = int(user32.GetKeyboardLayoutList(count, layouts))
    return tuple(int(layout) for layout in layouts[:copied] if layout and imm32.ImmIsIME(layout))


def _windows_open_status(user32: Any, imm32: Any, hwnd: int) -> bool | None:
    ime_window = imm32.ImmGetDefaultIMEWnd(hwnd)
    if not ime_window:
        return None
    return bool(user32.SendMessageW(ime_window, 0x0283, 0x0005, 0))


def _windows_toggle_hotkey(layout: int | None) -> int | None:
    if not layout:
        return None
    primary_language = (layout & 0xFFFF) & 0x03FF
    return {
        0x04: 0x10,  # Chinese: IME_CHOTKEY_IME_NONIME_TOGGLE
        0x11: 0x30,  # Japanese: IME_JHOTKEY_CLOSE_OPEN
        0x12: 0x52,  # Korean: IME_KHOTKEY_ENGLISH
        0x1E: 0x70,  # Thai: IME_THOTKEY_IME_NONIME_TOGGLE
    }.get(primary_language)


def _active_fcitx() -> tuple[str, str] | None:
    for command in ("fcitx5-remote", "fcitx-remote"):
        if not shutil.which(command):
            continue
        try:
            state = _run_command([command]).stdout.strip()
        except (ImeControlError, subprocess.SubprocessError):
            continue
        if state in {"1", "2"}:
            return command, state
    return None


def _run_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise ImeControlError(f"{' '.join(arguments)} failed: {detail}")
    return result


def _find_ibus_engine(*, want_ime: bool) -> str | None:
    output = _run_command(["ibus", "list-engine"]).stdout
    engines = re.findall(r"^\s*name:\s*(\S+)\s*$", output, flags=re.MULTILINE)
    return next(
        (
            engine
            for engine in engines
            if (not engine.startswith("xkb:") if want_ime else engine.startswith("xkb:"))
        ),
        None,
    )


def _load_macos_api() -> dict[str, Any]:
    carbon_path = ctypes.util.find_library("Carbon")
    core_foundation_path = ctypes.util.find_library("CoreFoundation")
    if not carbon_path or not core_foundation_path:
        raise ImeControlError("The macOS Carbon input source API is unavailable.")
    carbon = ctypes.CDLL(carbon_path)
    core_foundation = ctypes.CDLL(core_foundation_path)
    carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
    carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
    carbon.TISCreateInputSourceList.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    carbon.TISCreateInputSourceList.restype = ctypes.c_void_p
    carbon.TISSelectInputSource.argtypes = [ctypes.c_void_p]
    carbon.TISSelectInputSource.restype = ctypes.c_int32
    core_foundation.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
    core_foundation.CFBooleanGetValue.restype = ctypes.c_bool
    core_foundation.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    core_foundation.CFArrayGetCount.restype = ctypes.c_long
    core_foundation.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    core_foundation.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    constants = {
        "ascii": ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceIsASCIICapable").value,
        "enabled": ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceIsEnabled").value,
        "selectable": ctypes.c_void_p.in_dll(
            carbon, "kTISPropertyInputSourceIsSelectCapable"
        ).value,
    }
    return {"carbon": carbon, "core_foundation": core_foundation, **constants}


def _macos_property(api: dict[str, Any], source: int, name: str) -> bool:
    value = api["carbon"].TISGetInputSourceProperty(source, api[name])
    return bool(value and api["core_foundation"].CFBooleanGetValue(value))


def _select_macos_source(api: dict[str, Any], *, ascii_capable: bool) -> int:
    carbon = api["carbon"]
    core_foundation = api["core_foundation"]
    sources = carbon.TISCreateInputSourceList(None, False)
    if not sources:
        raise ImeControlError("macOS did not return any keyboard input sources.")
    try:
        for index in range(core_foundation.CFArrayGetCount(sources)):
            source = core_foundation.CFArrayGetValueAtIndex(sources, index)
            if (
                _macos_property(api, source, "enabled")
                and _macos_property(api, source, "selectable")
                and _macos_property(api, source, "ascii") is ascii_capable
            ):
                return int(carbon.TISSelectInputSource(source))
    finally:
        core_foundation.CFRelease(sources)
    kind = "direct-input" if ascii_capable else "input method"
    raise ImeControlError(f"macOS has no enabled {kind} source to select.")
