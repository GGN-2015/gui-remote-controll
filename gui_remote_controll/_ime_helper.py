from __future__ import annotations

import argparse
import json
import sys
import time

from .ime import ImeControlError, ImeController, _macos_current_source


def _payload(controller: ImeController) -> dict[str, object]:
    state = controller._macos_status_native()
    source = _macos_current_source(controller._macos_api())
    return {**state.as_message(), "sourceId": source.source_id}


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("operation", choices=("status", "set"))
    parser.add_argument("--enabled", choices=("true", "false"))
    parser.add_argument("--preferred-source")
    args = parser.parse_args()
    controller = ImeController(delegate_macos=False)
    try:
        if args.operation == "set":
            if args.enabled is None:
                parser.error("--enabled is required for set")
            enabled = args.enabled == "true"
            controller._macos_set_native(
                enabled,
                preferred_source_id=args.preferred_source,
            )
            for attempt in range(8):
                payload = _payload(controller)
                if payload["enabled"] is enabled:
                    break
                if attempt < 7:
                    time.sleep(0.05)
            else:
                raise ImeControlError("macOS did not apply the requested input source state.")
        else:
            payload = _payload(controller)
    except (ImeControlError, OSError, ValueError, AttributeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        controller.close()
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
