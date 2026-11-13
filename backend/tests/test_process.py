"""End-to-end lifecycle of the ``serve`` process as the Tauri shell drives it."""

from __future__ import annotations

import json
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def _request(port: int, token: str | None, path: str = "/api/v1/health") -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback test URL
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def _start(data_dir: Path, token: str) -> tuple[subprocess.Popen[str], int]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "pricepilot", "serve", "--data-dir", str(data_dir), "--token-stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(token + "\n")
    proc.stdin.flush()
    ready = json.loads(proc.stdout.readline())
    assert ready["event"] == "ready"
    return proc, int(ready["port"])


def _stop(proc: subprocess.Popen[str]) -> int:
    assert proc.stdin is not None
    proc.stdin.close()
    return proc.wait(timeout=15)


def test_serve_binds_loopback_random_port_and_exits_with_parent(tmp_path: Path) -> None:
    token = secrets.token_urlsafe(32)
    proc, port = _start(tmp_path, token)
    try:
        assert port > 0
        status, body = _request(port, token)
        assert status == 200
        assert body["status"] == "ok"
        status, body = _request(port, None)
        assert status == 401
        assert body["error"]["correlation_id"]

        # Not reachable on a non-loopback address of this machine (if it has one).
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("192.0.2.1", 9))  # TEST-NET, no packets are sent for UDP connect
            external_ip = probe.getsockname()[0]
            probe.close()
        except OSError:
            external_ip = None
        if external_ip and not external_ip.startswith("127."):
            with pytest.raises((ConnectionRefusedError, TimeoutError)):
                socket.create_connection((external_ip, port), timeout=1).close()
    finally:
        assert _stop(proc) == 0


def test_serve_persists_between_launches(tmp_path: Path) -> None:
    token = secrets.token_urlsafe(32)
    proc, port = _start(tmp_path, token)
    try:
        payload = json.dumps(
            {"name": "WS", "locale": "en", "base_currency": "USD", "country_code": "US"}
        ).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/workspaces",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback test URL
            ws_id = json.loads(resp.read())["id"]
    finally:
        _stop(proc)

    new_token = secrets.token_urlsafe(32)
    proc, port = _start(tmp_path, new_token)
    try:
        status, body = _request(port, new_token, f"/api/v1/workspaces/{ws_id}")
        assert status == 200
        assert body["name"] == "WS"
        status, _ = _request(port, token)  # previous launch token is no longer valid
        assert status == 401
    finally:
        _stop(proc)


def test_serve_refuses_short_token(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pricepilot", "serve", "--data-dir", str(tmp_path), "--token-stdin"],
        input="short\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["code"] == "CONFIG_ERROR"


def test_serve_processes_upload_in_background_thread(tmp_path: Path) -> None:
    import time
    from urllib.parse import quote

    fixture = (
        Path(__file__).resolve().parents[2]
        / "tests/fixtures/price-lists/Прайс_3_krovlya_pilomaterialy.xlsx"
    )
    token = secrets.token_urlsafe(32)
    proc, port = _start(tmp_path, token)
    base = f"http://127.0.0.1:{port}/api/v1"
    auth = {"Authorization": f"Bearer {token}"}
    try:
        ws_req = urllib.request.Request(  # noqa: S310 - loopback test URL
            f"{base}/workspaces",
            data=json.dumps(
                {"name": "WS", "locale": "ru-RU", "base_currency": "RUB", "country_code": "RU"}
            ).encode(),
            headers={**auth, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(ws_req, timeout=5) as resp:  # noqa: S310 - loopback
            ws_id = json.loads(resp.read())["id"]
        up = urllib.request.Request(  # noqa: S310 - loopback test URL
            f"{base}/documents?workspace_id={ws_id}",
            data=fixture.read_bytes(),
            headers={**auth, "X-File-Name": quote(fixture.name)},
            method="POST",
        )
        with urllib.request.urlopen(up, timeout=10) as resp:  # noqa: S310 - loopback
            run_id = json.loads(resp.read())["run"]["id"]
        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline:
            _code, run = _request(port, token, f"/api/v1/processing-runs/{run_id}")
            status = run["status"]
            if status in {"completed", "review_required", "failed"}:
                break
            time.sleep(0.1)
        assert status == "completed"
        assert run["counts"]["lines"] == 39
    finally:
        assert _stop(proc) == 0
