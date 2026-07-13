from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from . import __version__
from .app import create_app
from .config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gui-remote-controll",
        description="Share and control this desktop from a web browser.",
    )
    parser.add_argument(
        "--title",
        default="GUI Remote Controll",
        help="Title shown in the browser client (maximum 200 characters).",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Server bind host.")
    parser.add_argument("--port", type=int, default=8000, help="Server port.")
    parser.add_argument("--pin", help="Require this PIN before clients can use the server.")
    parser.add_argument("--fps", type=int, default=10, help="Maximum frame rate from 1 to 30.")
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=80,
        help="JPEG quality from 20 to 95.",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="Initial display index. Use 0 for the combined virtual desktop.",
    )
    parser.add_argument(
        "--view-only",
        action="store_true",
        help="Stream the desktop without accepting input events.",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Disable remote clipboard synchronization.",
    )
    parser.add_argument(
        "--no-cursor",
        action="store_true",
        help="Do not include the system cursor in Linux/X11 frames.",
    )
    parser.add_argument(
        "--max-clients",
        type=int,
        default=4,
        help="Maximum simultaneous WebSocket clients from 1 to 32.",
    )
    parser.add_argument(
        "--trusted-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="Additional allowed browser origin. May be repeated.",
    )
    parser.add_argument("--tls-certfile", type=Path, help="TLS certificate file.")
    parser.add_argument("--tls-keyfile", type=Path, help="TLS private key file.")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the local control URL after the server starts.",
    )
    parser.add_argument(
        "--no-elevate",
        action="store_true",
        help="Do not request administrator/root privileges before startup.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="Uvicorn log level.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--elevation-attempted", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--desktop-uid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--desktop-gid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--desktop-user", help=argparse.SUPPRESS)
    parser.add_argument("--desktop-home", help=argparse.SUPPRESS)
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings(
        title=args.title,
        host=args.host,
        port=args.port,
        pin=args.pin,
        fps=args.fps,
        jpeg_quality=args.jpeg_quality,
        monitor=args.monitor,
        view_only=args.view_only,
        clipboard_enabled=not args.no_clipboard,
        capture_cursor=not args.no_cursor,
        max_clients=args.max_clients,
        trusted_origins=tuple(args.trusted_origin),
        tls_enabled=bool(args.tls_certfile),
        desktop_uid=args.desktop_uid,
        desktop_gid=args.desktop_gid,
        desktop_user=args.desktop_user,
        desktop_home=args.desktop_home,
    )
    settings.validate()
    return settings


def _launch_elevated(raw_args: Sequence[str]) -> int:
    from py_admin_launch import AdminLaunchError, launch

    command = [
        sys.executable,
        "-m",
        "gui_remote_controll",
        "--elevation-attempted",
        *_desktop_session_args(),
        *raw_args,
    ]
    try:
        result = launch(command, cwd=os.getcwd(), wait=True)
    except AdminLaunchError as exc:
        raise RuntimeError(f"Administrator launch failed: {exc}") from exc
    return result.returncode if result.returncode is not None else 0


def _desktop_session_args() -> list[str]:
    if os.name == "nt" or not hasattr(os, "getuid"):
        return []
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0:
        return []
    try:
        import pwd

        account = pwd.getpwuid(uid)
    except (ImportError, KeyError):
        return ["--desktop-uid", str(uid), "--desktop-gid", str(gid)]
    return [
        "--desktop-uid",
        str(uid),
        "--desktop-gid",
        str(gid),
        "--desktop-user",
        account.pw_name,
        "--desktop-home",
        account.pw_dir,
    ]


def _validate_tls(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if bool(args.tls_certfile) != bool(args.tls_keyfile):
        parser.error("--tls-certfile and --tls-keyfile must be provided together")
    for value in (args.tls_certfile, args.tls_keyfile):
        if value is not None and not value.is_file():
            parser.error(f"file does not exist: {value}")


def _local_url(settings: Settings) -> str:
    scheme = "https" if settings.tls_enabled else "http"
    host = settings.host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{settings.port}"


def main(argv: Sequence[str] | None = None) -> None:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_args)
    _validate_tls(parser, args)

    if not args.no_elevate and not args.elevation_attempted:
        try:
            raise SystemExit(_launch_elevated(raw_args))
        except RuntimeError as exc:
            parser.exit(1, f"{exc}\n")

    try:
        settings = settings_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    if settings.host in {"0.0.0.0", "::"} and settings.pin is None:
        print(
            "WARNING: remote control is exposed without a PIN. "
            "Use --pin or bind to --host 127.0.0.1.",
            file=sys.stderr,
        )

    local_url = _local_url(settings)
    if args.open_browser:
        timer = threading.Timer(0.8, webbrowser.open, args=(local_url,))
        timer.daemon = True
        timer.start()

    print(f"GUI Remote Controll {__version__}: {local_url}")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=args.log_level,
        ws="auto",
        ws_max_size=settings.max_message_bytes,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        timeout_graceful_shutdown=3,
        ssl_certfile=str(args.tls_certfile) if args.tls_certfile else None,
        ssl_keyfile=str(args.tls_keyfile) if args.tls_keyfile else None,
    )


if __name__ == "__main__":
    main()
