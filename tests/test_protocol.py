from __future__ import annotations

import pytest

from gui_remote_controll.protocol import ProtocolError, validate_client_message


def validate(payload: object) -> dict[str, object]:
    return validate_client_message(payload, max_text_chars=10)


def test_pointer_message_is_normalized() -> None:
    assert validate({"type": "pointer", "event": "down", "button": "left", "x": 0.5, "y": 1}) == {
        "type": "pointer",
        "event": "down",
        "button": "left",
        "x": 0.5,
        "y": 1.0,
    }


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"type": "unknown"},
        {"type": "pointer", "event": "move", "x": -1, "y": 0},
        {"type": "pointer", "event": "down", "button": "extra", "x": 0, "y": 0},
        {"type": "monitor", "index": True},
        {"type": "text", "text": "01234567890"},
        {"type": "wheel", "dx": float("nan"), "dy": 0},
    ],
)
def test_invalid_messages_are_rejected(payload: object) -> None:
    with pytest.raises(ProtocolError):
        validate(payload)


def test_text_and_clipboard_share_size_limit() -> None:
    assert validate({"type": "text", "text": "hello"})["text"] == "hello"
    clipboard_set = validate(
        {"type": "clipboard_set", "text": "world", "requestId": "request-1"}
    )
    assert clipboard_set["text"] == "world"
    assert clipboard_set["requestId"] == "request-1"


def test_clipboard_get_accepts_a_known_digest() -> None:
    message = validate({"type": "clipboard_get", "knownDigest": "0123456789abcdef"})
    assert message == {
        "type": "clipboard_get",
        "knownDigest": "0123456789abcdef",
    }
