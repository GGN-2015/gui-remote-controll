from __future__ import annotations

import io
import os
import platform
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any

from .config import Settings
from .ime import DesktopSession, ImeControlError, ImeController, ImeState


class DesktopUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Screen:
    index: int
    left: int
    top: int
    width: int
    height: int
    name: str

    def as_message(self) -> dict[str, int | str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Frame:
    data: bytes
    screen: Screen


class DesktopBackend:
    """Lazy adapter around mss, pynput, Pillow, and pyperclip."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._capture_local = threading.local()
        self._control_lock = threading.RLock()
        self._initialized = False
        self._mss_module: Any = None
        self._image_module: Any = None
        self._mouse: Any = None
        self._keyboard: Any = None
        self._mouse_button: Any = None
        self._keyboard_key: Any = None
        self._keyboard_key_code: Any = None
        self._pyperclip: Any = None
        self._mouse_listener: Any = None
        self._keyboard_listener: Any = None
        self._local_input_callback: Callable[[], None] | None = None
        self._input_monitoring_error = "Local input monitoring has not started."
        self._ime = ImeController(
            DesktopSession(
                uid=settings.desktop_uid,
                gid=settings.desktop_gid,
                user=settings.desktop_user,
                home=settings.desktop_home,
            )
        )
        self._scroll_remainder_x = 0.0
        self._scroll_remainder_y = 0.0

    def initialize(self) -> None:
        with self._control_lock:
            if self._initialized:
                return
            self._check_linux_session()
            _enable_windows_dpi_awareness()
            try:
                # MSS must be imported before input libraries to preserve DPI coordinates.
                import mss
                from PIL import Image
                from pynput.keyboard import (
                    Controller as KeyboardController,
                )
                from pynput.keyboard import (
                    Key,
                    KeyCode,
                )
                from pynput.keyboard import (
                    Listener as KeyboardListener,
                )
                from pynput.mouse import Button
                from pynput.mouse import Controller as MouseController
                from pynput.mouse import Listener as MouseListener

                self._mss_module = mss
                self._image_module = Image
                self._mouse = MouseController()
                self._keyboard = KeyboardController()
                self._mouse_button = Button
                self._keyboard_key = Key
                self._keyboard_key_code = KeyCode
                if self.settings.clipboard_enabled:
                    import pyperclip

                    self._pyperclip = pyperclip
                self._probe_capture()
                self._start_input_monitoring(MouseListener, KeyboardListener)
            except Exception as exc:
                raise DesktopUnavailableError(_desktop_error_message(exc)) from exc
            self._initialized = True

    def set_local_input_callback(self, callback: Callable[[], None]) -> None:
        self._local_input_callback = callback

    @property
    def local_input_monitoring(self) -> bool:
        return bool(
            self._mouse_listener is not None
            and self._keyboard_listener is not None
            and self._mouse_listener.running
            and self._keyboard_listener.running
        )

    @property
    def local_input_monitoring_detail(self) -> str:
        if (
            self._mouse_listener is not None
            and self._keyboard_listener is not None
            and not self.local_input_monitoring
        ):
            return "Local input monitoring stopped unexpectedly."
        return self._input_monitoring_error

    def list_screens(self) -> tuple[Screen, ...]:
        self.initialize()
        try:
            with self._new_capture() as capture:
                return tuple(
                    self._screen_from_monitor(index, monitor)
                    for index, monitor in enumerate(capture.monitors)
                )
        except Exception as exc:
            raise DesktopUnavailableError(_desktop_error_message(exc)) from exc

    def capture(self, monitor_index: int) -> Frame:
        self.initialize()
        capture = getattr(self._capture_local, "capture", None)
        if capture is None:
            try:
                capture = self._new_capture()
            except Exception as exc:
                raise DesktopUnavailableError(_desktop_error_message(exc)) from exc
            self._capture_local.capture = capture
        try:
            monitors = capture.monitors
            if monitor_index >= len(monitors):
                raise DesktopUnavailableError(f"Monitor {monitor_index} is no longer available.")
            monitor = monitors[monitor_index]
            shot = capture.grab(monitor)
            image = self._image_module.frombytes("RGB", shot.size, shot.rgb)
            output = io.BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=self.settings.jpeg_quality,
                optimize=False,
                subsampling=2,
            )
            return Frame(output.getvalue(), self._screen_from_monitor(monitor_index, monitor))
        except DesktopUnavailableError:
            raise
        except Exception as exc:
            raise DesktopUnavailableError(_desktop_error_message(exc)) from exc

    def execute(self, message: dict[str, Any], screen: Screen) -> None:
        self.initialize()
        with self._control_lock:
            message_type = message["type"]
            if message_type == "pointer":
                x = screen.left + round(message["x"] * max(0, screen.width - 1))
                y = screen.top + round(message["y"] * max(0, screen.height - 1))
                self._mouse.position = (x, y)
                if message["event"] != "move":
                    button = getattr(self._mouse_button, message["button"])
                    action = (
                        self._mouse.press if message["event"] == "down" else self._mouse.release
                    )
                    action(button)
                return
            if message_type == "wheel":
                self._scroll_remainder_x -= message["dx"]
                self._scroll_remainder_y -= message["dy"]
                step_x = int(self._scroll_remainder_x)
                step_y = int(self._scroll_remainder_y)
                self._scroll_remainder_x -= step_x
                self._scroll_remainder_y -= step_y
                if step_x or step_y:
                    self._mouse.scroll(step_x, step_y)
                return
            if message_type == "key":
                key = self._resolve_key(message["key"])
                action = (
                    self._keyboard.press if message["event"] == "down" else self._keyboard.release
                )
                action(key)
                return
            if message_type == "text":
                self._keyboard.type(message["text"])

    def release_inputs(self, keys: set[str], buttons: set[str]) -> None:
        if not self._initialized:
            return
        with self._control_lock:
            for key_name in keys:
                with suppress(Exception):
                    self._keyboard.release(self._resolve_key(key_name))
            for button_name in buttons:
                with suppress(Exception):
                    self._mouse.release(getattr(self._mouse_button, button_name))

    def clipboard_get(self) -> str:
        self.initialize()
        if not self.settings.clipboard_enabled or self._pyperclip is None:
            raise DesktopUnavailableError("Clipboard synchronization is disabled.")
        try:
            return str(self._pyperclip.paste())[: self.settings.max_clipboard_chars]
        except Exception as exc:
            raise DesktopUnavailableError(f"Clipboard read failed: {exc}") from exc

    def clipboard_set(self, text: str) -> None:
        self.initialize()
        if not self.settings.clipboard_enabled or self._pyperclip is None:
            raise DesktopUnavailableError("Clipboard synchronization is disabled.")
        try:
            self._pyperclip.copy(text)
        except Exception as exc:
            raise DesktopUnavailableError(f"Clipboard write failed: {exc}") from exc

    def ime_status(self) -> ImeState:
        self.initialize()
        return self._ime.status()

    def ime_set(self, enabled: bool) -> ImeState:
        self.initialize()
        try:
            return self._ime.set_enabled(enabled)
        except ImeControlError as exc:
            raise DesktopUnavailableError(f"IME operation failed: {exc}") from exc

    def shutdown(self) -> None:
        for listener in (self._mouse_listener, self._keyboard_listener):
            if listener is not None:
                with suppress(Exception):
                    listener.stop()
        self._mouse_listener = None
        self._keyboard_listener = None
        self._ime.close()

    def _probe_capture(self) -> None:
        with self._new_capture() as capture:
            if len(capture.monitors) < 2:
                raise DesktopUnavailableError("No physical display was detected.")

    def _new_capture(self) -> Any:
        options = {}
        if platform.system() == "Linux" and self.settings.capture_cursor:
            options["with_cursor"] = True
        return self._mss_module.MSS(**options)

    def _start_input_monitoring(self, mouse_listener: Any, keyboard_listener: Any) -> None:
        listeners: tuple[Any, ...] = ()
        try:
            listeners = (
                mouse_listener(
                    on_move=self._handle_local_input,
                    on_click=self._handle_local_input,
                    on_scroll=self._handle_local_input,
                ),
                keyboard_listener(
                    on_press=self._handle_local_input,
                    on_release=self._handle_local_input,
                ),
            )
            for listener in listeners:
                listener.start()
            for listener in listeners:
                listener.wait()
        except Exception as exc:
            for listener in listeners:
                with suppress(Exception):
                    listener.stop()
            self._input_monitoring_error = f"Local input monitoring failed: {exc}"
            return
        self._mouse_listener, self._keyboard_listener = listeners
        self._input_monitoring_error = "Local input monitoring is active."

    def _handle_local_input(self, *args: Any) -> None:
        injected = bool(args and args[-1] is True)
        if not injected and self._local_input_callback is not None:
            self._local_input_callback()

    def _resolve_key(self, name: str) -> Any:
        aliases = {
            "Alt": "alt",
            "AltGraph": "alt_gr",
            "ArrowDown": "down",
            "ArrowLeft": "left",
            "ArrowRight": "right",
            "ArrowUp": "up",
            "Backspace": "backspace",
            "CapsLock": "caps_lock",
            "Control": "ctrl",
            "Delete": "delete",
            "End": "end",
            "Enter": "enter",
            "Escape": "esc",
            "F1": "f1",
            "F2": "f2",
            "F3": "f3",
            "F4": "f4",
            "F5": "f5",
            "F6": "f6",
            "F7": "f7",
            "F8": "f8",
            "F9": "f9",
            "F10": "f10",
            "F11": "f11",
            "F12": "f12",
            "Home": "home",
            "Insert": "insert",
            "Meta": "cmd",
            "NumLock": "num_lock",
            "OS": "cmd",
            "PageDown": "page_down",
            "PageUp": "page_up",
            "Pause": "pause",
            "PrintScreen": "print_screen",
            "ScrollLock": "scroll_lock",
            "Shift": "shift",
            "Tab": "tab",
        }
        key_name = aliases.get(name)
        if key_name is not None:
            key = getattr(self._keyboard_key, key_name, None)
            if key is None:
                raise DesktopUnavailableError(f"Key {name!r} is not supported on this platform.")
            return key
        if len(name) == 1:
            return self._keyboard_key_code.from_char(name)
        raise DesktopUnavailableError(f"Key {name!r} is not supported.")

    @staticmethod
    def _screen_from_monitor(index: int, monitor: dict[str, int]) -> Screen:
        default_name = "All displays" if index == 0 else f"Display {index}"
        return Screen(
            index=index,
            left=int(monitor["left"]),
            top=int(monitor["top"]),
            width=int(monitor["width"]),
            height=int(monitor["height"]),
            name=default_name if index == 0 else str(monitor.get("name") or default_name),
        )

    @staticmethod
    def _check_linux_session() -> None:
        if platform.system() != "Linux":
            return
        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            raise DesktopUnavailableError(
                "Native Wayland blocks portable screen capture and input injection. "
                "Start the server from an X11/XWayland desktop session."
            )
        if not os.environ.get("DISPLAY"):
            raise DesktopUnavailableError(
                "No X11 DISPLAY is available. Start the server inside the "
                "signed-in desktop session."
            )


def _enable_windows_dpi_awareness() -> None:
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        with suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()


def _desktop_error_message(exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    system = platform.system()
    if system == "Darwin":
        return (
            f"Desktop access failed: {detail}. Grant Screen Recording and Accessibility "
            "permissions to the terminal or Python process, then restart the server."
        )
    if system == "Windows":
        return (
            f"Desktop access failed: {detail}. Run the server inside the interactive user session."
        )
    return f"Desktop access failed: {detail}. Check DISPLAY and X11 permissions."
