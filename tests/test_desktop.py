from __future__ import annotations

from dataclasses import dataclass, field

from gui_remote_controll.config import Settings
from gui_remote_controll.desktop import DesktopBackend, Screen


@dataclass
class FakeMouse:
    position: tuple[int, int] = (0, 0)
    scrolls: list[tuple[int, int]] = field(default_factory=list)

    def scroll(self, dx: int, dy: int) -> None:
        self.scrolls.append((dx, dy))


def ready_backend() -> DesktopBackend:
    backend = DesktopBackend(Settings())
    backend._initialized = True
    backend._mouse = FakeMouse()
    return backend


def test_pointer_coordinates_include_monitor_offset() -> None:
    backend = ready_backend()
    screen = Screen(index=2, left=-1920, top=100, width=1920, height=1080, name="Display 2")
    backend.execute({"type": "pointer", "event": "move", "x": 1.0, "y": 0.0}, screen)
    assert backend._mouse.position == (-1, 100)


def test_fractional_scroll_is_accumulated_into_native_steps() -> None:
    backend = ready_backend()
    screen = Screen(index=1, left=0, top=0, width=100, height=100, name="Display 1")
    for _ in range(4):
        backend.execute({"type": "wheel", "dx": 0.0, "dy": 0.3}, screen)
    assert backend._mouse.scrolls == [(0, -1)]


def test_monitor_zero_is_named_combined_desktop() -> None:
    screen = DesktopBackend._screen_from_monitor(
        0, {"left": -1920, "top": 0, "width": 3840, "height": 1080}
    )
    assert screen.name == "All displays"
    assert screen.left == -1920


def test_only_physical_input_reaches_priority_callback() -> None:
    backend = ready_backend()
    events: list[str] = []
    backend.set_local_input_callback(lambda: events.append("local"))

    backend._handle_local_input("event", True)
    backend._handle_local_input("event", False)

    assert events == ["local"]
