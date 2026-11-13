"""CLI entrypoint.

``serve``   — run the local API on 127.0.0.1. With ``--port 0`` the OS picks a free port.
              After startup a single JSON line ``{"event":"ready","port":N}`` is written to
              stdout for the parent process (Tauri shell or dev script).
              With ``--token-stdin`` the first stdin line is the capability token and the
              process exits gracefully when stdin is closed (parent died or asked to stop).
``openapi`` — print or verify the OpenAPI document (no server, no database).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from pricepilot.api.app import create_app, openapi_document
from pricepilot.config import LOOPBACK_HOST, TAURI_ORIGINS, ConfigError, Settings, default_data_dir
from pricepilot.infrastructure.logging import configure_logging

TOKEN_ENV = "PRICEPILOT_API_TOKEN"  # noqa: S105 - variable name, not a secret


def _read_token(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().strip()
    return os.environ.get(TOKEN_ENV, "").strip()


def _bind_loopback(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((LOOPBACK_HOST, port))
    sock.listen(128)
    sock.set_inheritable(False)
    return sock


def serve(args: argparse.Namespace) -> int:
    token = _read_token(args.token_stdin)
    origins = tuple(args.allow_origin) if args.allow_origin else TAURI_ORIGINS
    try:
        settings = Settings(
            data_dir=Path(args.data_dir) if args.data_dir else default_data_dir(),
            api_token=token,
            port=args.port,
            allowed_origins=origins,
            log_level=args.log_level,
        )
    except ConfigError as exc:
        sys.stdout.write(
            json.dumps({"event": "error", "code": "CONFIG_ERROR", "message": str(exc)}) + "\n"
        )
        return 2

    configure_logging(settings.log_level, settings.log_dir if settings.log_to_file else None)
    sock = _bind_loopback(settings.port)
    port = sock.getsockname()[1]

    def announce_ready() -> None:
        sys.stdout.write(json.dumps({"event": "ready", "port": port}) + "\n")
        sys.stdout.flush()

    app = create_app(settings, on_ready=announce_ready)
    config = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)

    if args.token_stdin:

        def watch_parent() -> None:
            for _ in sys.stdin:
                pass
            server.should_exit = True

        threading.Thread(target=watch_parent, name="parent-watch", daemon=True).start()

    server.run(sockets=[sock])
    return 0


def openapi(args: argparse.Namespace) -> int:
    settings = Settings(data_dir=Path("."), api_token="x" * 32)
    document = (
        json.dumps(openapi_document(create_app(settings)), indent=2, ensure_ascii=False) + "\n"
    )
    if args.check:
        target = Path(args.check)
        if not target.exists() or target.read_text(encoding="utf-8") != document:
            sys.stderr.write(f"OpenAPI contract is out of date: {target}\n")
            return 1
        return 0
    if args.output:
        Path(args.output).write_text(document, encoding="utf-8")
    else:
        sys.stdout.write(document)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pricepilot")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the local API on 127.0.0.1")
    p_serve.add_argument("--data-dir", help="directory for the database and logs")
    p_serve.add_argument("--port", type=int, default=0, help="0 = random free port")
    p_serve.add_argument("--token-stdin", action="store_true", help="read token from stdin")
    p_serve.add_argument("--allow-origin", action="append", help="allowed UI origin (repeatable)")
    p_serve.add_argument("--log-level", default="INFO")
    p_serve.set_defaults(func=serve)

    p_openapi = sub.add_parser("openapi", help="emit the OpenAPI document")
    group = p_openapi.add_mutually_exclusive_group()
    group.add_argument("--output")
    group.add_argument("--check", help="fail if the given file differs")
    p_openapi.set_defaults(func=openapi)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
