from __future__ import annotations

import ast
import ctypes
import ctypes.util
import json
import os
import platform
import shutil
import subprocess
import sys
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


@dataclass(frozen=True, slots=True)
class DesktopSession:
    uid: int | None = None
    gid: int | None = None
    user: str | None = None
    home: str | None = None


@dataclass(frozen=True, slots=True)
class MacInputSource:
    source_id: str
    bundle_id: str
    kind: str
    ascii_capable: bool

    @property
    def ime_enabled(self) -> bool:
        return self.kind in {"input_method", "input_mode"} and not self.ascii_capable


class ImeController:
    """Best-effort native input method controller for the interactive desktop."""

    def __init__(
        self,
        desktop_session: DesktopSession | None = None,
        *,
        delegate_macos: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._desktop_session = _normalized_desktop_session(desktop_session)
        self._delegate_macos = delegate_macos
        self._saved_windows_layout: int | None = None
        self._saved_ibus_engine: str | None = None
        self._saved_ibus_direct_engine: str | None = None
        self._saved_macos_source_id: str | None = None
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
            except (
                ImeControlError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
                AttributeError,
                ctypes.ArgumentError,
            ) as exc:
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
            attempts = 8
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
            self._saved_macos_source_id = None

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
            _windows_ime_control(user32, ime_window, 0x0006, int(enabled))
        elif not context_applied:
            raise ImeControlError("The foreground window has no accessible IME.")

        open_status = _windows_open_status(user32, imm32, hwnd)
        if open_status is not None and open_status is not enabled:
            layout = _windows_foreground_layout(user32, hwnd)
            hotkey = _windows_toggle_hotkey(layout)
            if hotkey is not None:
                imm32.ImmSimulateHotKey(hwnd, hotkey)

    def _linux_status(self) -> ImeState:
        fcitx = _active_fcitx(self._desktop_session)
        if fcitx is not None:
            command, state = fcitx
            return ImeState(True, state == "2", command)
        if shutil.which("ibus"):
            engine = _run_desktop_command(["ibus", "engine"], self._desktop_session).stdout.strip()
            if not engine:
                raise ImeControlError("IBus did not report an active engine.")
            return ImeState(True, not engine.startswith("xkb:"), f"IBus ({engine})")
        return ImeState(
            False,
            None,
            "Install or start Fcitx 4/5 or IBus in the current desktop session.",
        )

    def _linux_set(self, enabled: bool) -> None:
        fcitx = _active_fcitx(self._desktop_session)
        if fcitx is not None:
            command, _ = fcitx
            _run_desktop_command([command, "-o" if enabled else "-c"], self._desktop_session)
            return
        if not shutil.which("ibus"):
            raise ImeControlError(
                "Install or start Fcitx 4/5 or IBus in the current desktop session."
            )
        current = _run_desktop_command(["ibus", "engine"], self._desktop_session).stdout.strip()
        if not current:
            raise ImeControlError("IBus did not report an active engine.")
        engines = _ibus_engines(self._desktop_session)
        if enabled:
            if current.startswith("xkb:"):
                self._saved_ibus_direct_engine = current
            target = (
                self._saved_ibus_engine
                if self._saved_ibus_engine in engines
                else _find_ibus_engine(engines, want_ime=True, session=self._desktop_session)
            )
            if target is None:
                raise ImeControlError("IBus has no enabled input method engine to restore.")
        else:
            if not current.startswith("xkb:"):
                self._saved_ibus_engine = current
            target = (
                self._saved_ibus_direct_engine
                if self._saved_ibus_direct_engine in engines
                else _find_ibus_engine(engines, want_ime=False, session=self._desktop_session)
            )
            if target is None:
                raise ImeControlError("IBus has no XKB engine available for direct input.")
        _run_desktop_command(["ibus", "engine", target], self._desktop_session)

    def _macos_status(self) -> ImeState:
        if self._should_delegate_macos():
            payload = self._run_macos_helper(["status"])
            return _ime_state_from_payload(payload)
        return self._macos_status_native()

    def _macos_set(self, enabled: bool) -> None:
        if self._should_delegate_macos():
            current = self._run_macos_helper(["status"])
            if not enabled and current.get("enabled") is True:
                source_id = current.get("sourceId")
                if isinstance(source_id, str) and source_id:
                    self._saved_macos_source_id = source_id
            arguments = ["set", "--enabled", "true" if enabled else "false"]
            if enabled and self._saved_macos_source_id:
                arguments.extend(["--preferred-source", self._saved_macos_source_id])
            self._run_macos_helper(arguments)
            return
        self._macos_set_native(enabled, preferred_source_id=self._saved_macos_source_id)

    def _macos_status_native(self) -> ImeState:
        source = _macos_current_source(self._macos_api())
        return ImeState(
            True,
            source.ime_enabled,
            f"macOS Text Input Source ({source.source_id or source.kind})",
        )

    def _macos_set_native(self, enabled: bool, *, preferred_source_id: str | None = None) -> None:
        api = self._macos_api()
        current = _macos_current_source(api)
        if not enabled and current.ime_enabled:
            self._saved_macos_source_id = current.source_id
        preferred = preferred_source_id or (self._saved_macos_source_id if enabled else None)
        _select_macos_source(
            api,
            enabled=enabled,
            preferred_source_id=preferred,
            preferred_bundle_id=current.bundle_id if not enabled else None,
        )

    def _should_delegate_macos(self) -> bool:
        return bool(
            self._delegate_macos
            and self._desktop_session is not None
            and self._desktop_session.uid is not None
            and hasattr(os, "geteuid")
            and os.geteuid() == 0
        )

    def _run_macos_helper(self, arguments: list[str]) -> dict[str, Any]:
        if self._desktop_session is None:
            raise ImeControlError("The signed-in macOS desktop session is unavailable.")
        result = _run_command(
            [sys.executable, "-m", "gui_remote_controll._ime_helper", *arguments],
            session=self._desktop_session,
            macos_session=True,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ImeControlError("The macOS desktop IME helper returned invalid data.") from exc
        if not isinstance(payload, dict):
            raise ImeControlError("The macOS desktop IME helper returned invalid data.")
        return payload

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
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
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
    return bool(_windows_ime_control(user32, ime_window, 0x0005, 0))


def _windows_ime_control(user32: Any, ime_window: int, command: int, value: int) -> int:
    send_with_timeout = getattr(user32, "SendMessageTimeoutW", None)
    if not callable(send_with_timeout):
        return int(user32.SendMessageW(ime_window, 0x0283, command, value))
    result = ctypes.c_void_p()
    sent = send_with_timeout(
        ime_window,
        0x0283,
        command,
        value,
        0x0002 | 0x0001,
        250,
        ctypes.byref(result),
    )
    if not sent:
        raise ImeControlError("The foreground application's IME window did not respond.")
    return int(result.value or 0)


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


def _normalized_desktop_session(session: DesktopSession | None) -> DesktopSession | None:
    if os.name == "nt" or not hasattr(os, "geteuid"):
        return None
    current_uid = os.geteuid()
    if session is None and current_uid == 0:
        raw_uid = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
        if raw_uid is None and platform.system() == "Linux":
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
            candidate = os.path.basename(runtime_dir.rstrip(os.sep))
            raw_uid = candidate if candidate.isdigit() else None
        if raw_uid is None and platform.system() == "Darwin":
            try:
                console_uid = os.stat("/dev/console").st_uid
            except OSError:
                console_uid = 0
            raw_uid = str(console_uid) if console_uid > 0 else None
        raw_gid = os.environ.get("SUDO_GID")
        try:
            session = DesktopSession(
                uid=int(raw_uid) if raw_uid is not None else None,
                gid=int(raw_gid) if raw_gid is not None else None,
            )
        except ValueError:
            session = None
    if session is None or session.uid is None or session.uid in {0, current_uid}:
        return None
    try:
        import pwd

        account = pwd.getpwuid(session.uid)
    except (ImportError, KeyError):
        return session
    return DesktopSession(
        uid=session.uid,
        gid=session.gid if session.gid is not None else account.pw_gid,
        user=session.user or account.pw_name,
        home=session.home or account.pw_dir,
    )


def _active_fcitx(session: DesktopSession | None = None) -> tuple[str, str] | None:
    for command in ("fcitx5-remote", "fcitx-remote"):
        if not shutil.which(command):
            continue
        try:
            state = _run_desktop_command([command], session).stdout.strip()
        except (ImeControlError, subprocess.SubprocessError):
            continue
        if state in {"1", "2"}:
            return command, state
    return None


def _run_desktop_command(
    arguments: list[str], session: DesktopSession | None
) -> subprocess.CompletedProcess[str]:
    if session is None:
        return _run_command(arguments)
    return _run_command(arguments, session=session)


def _run_command(
    arguments: list[str],
    *,
    session: DesktopSession | None = None,
    macos_session: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = list(arguments)
    environment = os.environ.copy()
    if session is not None:
        if session.home:
            environment["HOME"] = session.home
        if session.user:
            environment["USER"] = session.user
            environment["LOGNAME"] = session.user
        command = _desktop_session_command(command, session, macos_session=macos_session)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
        env=environment,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise ImeControlError(f"{' '.join(arguments)} failed: {detail}")
    return result


def _desktop_session_command(
    arguments: list[str], session: DesktopSession, *, macos_session: bool
) -> list[str]:
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or session.uid is None:
        return arguments
    if macos_session and platform.system() == "Darwin":
        launchctl = shutil.which("launchctl") or "/bin/launchctl"
        sudo = shutil.which("sudo") or "/usr/bin/sudo"
        user = session.user or f"#{session.uid}"
        return [
            launchctl,
            "asuser",
            str(session.uid),
            sudo,
            "-u",
            user,
            "--",
            *_desktop_environment_command(arguments, session),
        ]
    user_command = _desktop_environment_command(arguments, session)
    setpriv = shutil.which("setpriv")
    if setpriv and session.gid is not None:
        return [
            setpriv,
            "--reuid",
            str(session.uid),
            "--regid",
            str(session.gid),
            "--init-groups",
            "--",
            *user_command,
        ]
    runuser = shutil.which("runuser")
    if runuser and session.user:
        return [runuser, "-u", session.user, "--", *user_command]
    sudo = shutil.which("sudo")
    if sudo:
        user = session.user or f"#{session.uid}"
        return [sudo, "-u", user, "--", *user_command]
    raise ImeControlError("No tool is available to enter the signed-in desktop user's session.")


def _desktop_environment_command(arguments: list[str], session: DesktopSession) -> list[str]:
    values = {
        name: value
        for name in (
            "DISPLAY",
            "XAUTHORITY",
            "WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
        )
        if (value := os.environ.get(name))
    }
    if session.home:
        values["HOME"] = session.home
    if session.user:
        values["USER"] = session.user
        values["LOGNAME"] = session.user
    if not values:
        return arguments
    env_executable = shutil.which("env") or "/usr/bin/env"
    return [env_executable, *(f"{name}={value}" for name, value in values.items()), *arguments]


def _ibus_engines(session: DesktopSession | None) -> tuple[str, ...]:
    output = _run_desktop_command(["ibus", "list-engine", "--name-only"], session).stdout
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def _ibus_preload_engines(session: DesktopSession | None) -> tuple[str, ...]:
    if not shutil.which("gsettings"):
        return ()
    try:
        output = _run_desktop_command(
            ["gsettings", "get", "org.freedesktop.ibus.general", "preload-engines"],
            session,
        ).stdout.strip()
        if output.startswith("@as "):
            output = output[4:]
        parsed = ast.literal_eval(output)
    except (ImeControlError, OSError, subprocess.SubprocessError, ValueError, SyntaxError):
        return ()
    if not isinstance(parsed, (list, tuple)):
        return ()
    return tuple(item for item in parsed if isinstance(item, str) and item)


def _find_ibus_engine(
    engines: tuple[str, ...],
    *,
    want_ime: bool,
    session: DesktopSession | None,
) -> str | None:
    available = set(engines)
    preferred = tuple(engine for engine in _ibus_preload_engines(session) if engine in available)
    candidates = (*preferred, *(engine for engine in engines if engine not in preferred))
    return next(
        (
            engine
            for engine in candidates
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
    core_foundation.CFEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    core_foundation.CFEqual.restype = ctypes.c_bool
    core_foundation.CFStringGetLength.argtypes = [ctypes.c_void_p]
    core_foundation.CFStringGetLength.restype = ctypes.c_long
    core_foundation.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
    core_foundation.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
    core_foundation.CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]
    core_foundation.CFStringGetCString.restype = ctypes.c_bool
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    constants = {
        "ascii": ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceIsASCIICapable").value,
        "enabled": ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceIsEnabled").value,
        "selectable": ctypes.c_void_p.in_dll(
            carbon, "kTISPropertyInputSourceIsSelectCapable"
        ).value,
        "source_id": ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceID").value,
        "bundle_id": ctypes.c_void_p.in_dll(carbon, "kTISPropertyBundleID").value,
        "source_type": ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceType").value,
        "type_layout": ctypes.c_void_p.in_dll(carbon, "kTISTypeKeyboardLayout").value,
        "type_input_method": ctypes.c_void_p.in_dll(
            carbon, "kTISTypeKeyboardInputMethodWithoutModes"
        ).value,
        "type_mode_enabled_method": ctypes.c_void_p.in_dll(
            carbon, "kTISTypeKeyboardInputMethodModeEnabled"
        ).value,
        "type_input_mode": ctypes.c_void_p.in_dll(carbon, "kTISTypeKeyboardInputMode").value,
    }
    return {"carbon": carbon, "core_foundation": core_foundation, **constants}


def _macos_property(api: dict[str, Any], source: int, name: str) -> bool:
    value = api["carbon"].TISGetInputSourceProperty(source, api[name])
    return bool(value and api["core_foundation"].CFBooleanGetValue(value))


def _macos_string_property(api: dict[str, Any], source: int, name: str) -> str:
    value = api["carbon"].TISGetInputSourceProperty(source, api[name])
    if not value:
        return ""
    core_foundation = api["core_foundation"]
    length = core_foundation.CFStringGetLength(value)
    size = core_foundation.CFStringGetMaximumSizeForEncoding(length, 0x08000100) + 1
    if size <= 0:
        return ""
    buffer = ctypes.create_string_buffer(size)
    if not core_foundation.CFStringGetCString(value, buffer, size, 0x08000100):
        return ""
    return buffer.value.decode("utf-8", errors="replace")


def _macos_source_kind(api: dict[str, Any], source: int) -> str:
    source_type = api["carbon"].TISGetInputSourceProperty(source, api["source_type"])
    if not source_type:
        return "other"
    core_foundation = api["core_foundation"]
    if core_foundation.CFEqual(source_type, api["type_layout"]):
        return "layout"
    if core_foundation.CFEqual(source_type, api["type_input_mode"]):
        return "input_mode"
    if core_foundation.CFEqual(source_type, api["type_input_method"]):
        return "input_method"
    if core_foundation.CFEqual(source_type, api["type_mode_enabled_method"]):
        return "input_method_parent"
    return "other"


def _macos_source_info(api: dict[str, Any], source: int) -> MacInputSource:
    return MacInputSource(
        source_id=_macos_string_property(api, source, "source_id"),
        bundle_id=_macos_string_property(api, source, "bundle_id"),
        kind=_macos_source_kind(api, source),
        ascii_capable=_macos_property(api, source, "ascii"),
    )


def _macos_current_source(api: dict[str, Any]) -> MacInputSource:
    source = api["carbon"].TISCopyCurrentKeyboardInputSource()
    if not source:
        raise ImeControlError("macOS did not report a current keyboard input source.")
    try:
        return _macos_source_info(api, source)
    finally:
        api["core_foundation"].CFRelease(source)


def _select_macos_source(
    api: dict[str, Any],
    *,
    enabled: bool,
    preferred_source_id: str | None,
    preferred_bundle_id: str | None,
) -> None:
    carbon = api["carbon"]
    core_foundation = api["core_foundation"]
    sources = carbon.TISCreateInputSourceList(None, False)
    if not sources:
        raise ImeControlError("macOS did not return any keyboard input sources.")
    try:
        best_source = None
        best_rank = 100
        for index in range(core_foundation.CFArrayGetCount(sources)):
            source = core_foundation.CFArrayGetValueAtIndex(sources, index)
            if not (
                _macos_property(api, source, "enabled")
                and _macos_property(api, source, "selectable")
            ):
                continue
            info = _macos_source_info(api, source)
            if enabled:
                if not info.ime_enabled:
                    continue
            elif not info.ascii_capable or info.kind == "input_method_parent":
                continue
            rank = 4
            if preferred_source_id and info.source_id == preferred_source_id:
                rank = 0
            elif not enabled and preferred_bundle_id and info.bundle_id == preferred_bundle_id:
                rank = 1
            elif (not enabled and info.kind == "layout") or (enabled and info.kind == "input_mode"):
                rank = 2
            if rank < best_rank:
                best_source = source
                best_rank = rank
        if best_source:
            result = int(carbon.TISSelectInputSource(best_source))
            if result != 0:
                raise ImeControlError(f"macOS rejected the input source change ({result}).")
            return
    finally:
        core_foundation.CFRelease(sources)
    kind = "input method" if enabled else "direct-input"
    raise ImeControlError(f"macOS has no enabled {kind} source to select.")


def _ime_state_from_payload(payload: dict[str, Any]) -> ImeState:
    supported = payload.get("supported")
    enabled = payload.get("enabled")
    detail = payload.get("detail")
    if not isinstance(supported, bool) or enabled is not None and not isinstance(enabled, bool):
        raise ImeControlError("The macOS desktop IME helper returned invalid state data.")
    if not isinstance(detail, str):
        raise ImeControlError("The macOS desktop IME helper returned invalid state data.")
    return ImeState(supported, enabled, detail)
