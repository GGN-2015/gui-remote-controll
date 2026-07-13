from __future__ import annotations

import math
from typing import Any


class ProtocolError(ValueError):
    pass


_MOUSE_EVENTS = {"move", "down", "up"}
_MOUSE_BUTTONS = {"left", "middle", "right"}
_KEY_EVENTS = {"down", "up"}


def validate_client_message(payload: Any, *, max_text_chars: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProtocolError("Message must be a JSON object.")
    message_type = _short_string(payload.get("type"), "type", 32)

    if message_type == "pointer":
        event = _short_string(payload.get("event"), "event", 8)
        if event not in _MOUSE_EVENTS:
            raise ProtocolError("Unsupported pointer event.")
        result: dict[str, Any] = {
            "type": message_type,
            "event": event,
            "x": _unit_float(payload.get("x"), "x"),
            "y": _unit_float(payload.get("y"), "y"),
        }
        if event != "move":
            button = _short_string(payload.get("button"), "button", 12)
            if button not in _MOUSE_BUTTONS:
                raise ProtocolError("Unsupported mouse button.")
            result["button"] = button
        return result

    if message_type == "wheel":
        return {
            "type": message_type,
            "dx": _bounded_float(payload.get("dx", 0), "dx", -20, 20),
            "dy": _bounded_float(payload.get("dy", 0), "dy", -20, 20),
        }

    if message_type == "key":
        event = _short_string(payload.get("event"), "event", 8)
        if event not in _KEY_EVENTS:
            raise ProtocolError("Unsupported key event.")
        return {
            "type": message_type,
            "event": event,
            "key": _short_string(payload.get("key"), "key", 64),
            "code": _short_string(payload.get("code", ""), "code", 64),
            "repeat": bool(payload.get("repeat", False)),
        }

    if message_type in {"text", "clipboard_set"}:
        text = payload.get("text")
        if not isinstance(text, str):
            raise ProtocolError("text must be a string.")
        if len(text) > max_text_chars:
            raise ProtocolError(f"text exceeds the {max_text_chars} character limit.")
        return {"type": message_type, "text": text}

    if message_type == "monitor":
        index = payload.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ProtocolError("monitor index must be a non-negative integer.")
        return {"type": message_type, "index": index}

    if message_type in {"clipboard_get", "ping"}:
        return {"type": message_type}

    raise ProtocolError("Unsupported message type.")


def _short_string(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ProtocolError(f"{name} must be a string no longer than {limit} characters.")
    return value


def _unit_float(value: Any, name: str) -> float:
    return _bounded_float(value, name, 0, 1)


def _bounded_float(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{name} must be a number.")
    converted = float(value)
    if not math.isfinite(converted) or converted < low or converted > high:
        raise ProtocolError(f"{name} must be between {low} and {high}.")
    return converted
