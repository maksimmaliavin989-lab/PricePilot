"""Runtime settings for the local API process."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_data_dir

# The local API never listens on anything but IPv4 loopback. There is deliberately
# no option to change it: a cloud deployment is a different composition root.
LOOPBACK_HOST = "127.0.0.1"
ALLOWED_HOST_NAMES = frozenset({"127.0.0.1", "localhost"})
MIN_TOKEN_LENGTH = 32
MAX_JSON_BODY_BYTES = 1_000_000

# Origins Tauri 2 uses for the bundled webview (macOS/Linux and Windows).
TAURI_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")


class ConfigError(ValueError):
    pass


def default_data_dir() -> Path:
    return Path(user_data_dir("PricePilot", appauthor=False))


def _validate_origin(origin: str) -> str:
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https", "tauri"} or not parts.hostname:
        raise ConfigError("origin must be scheme://host[:port]")
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ConfigError("origin must not contain a path")
    if parts.scheme != "tauri" and parts.hostname not in {
        "localhost",
        "127.0.0.1",
        "tauri.localhost",
    }:
        raise ConfigError("only local origins are allowed")
    return origin.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    api_token: str
    port: int = 0
    allowed_origins: tuple[str, ...] = TAURI_ORIGINS
    log_level: str = "INFO"
    log_to_file: bool = True
    enable_docs: bool = False
    max_upload_bytes: int = 50 * 1024 * 1024
    worker_mode: str = "thread"  # "thread" (app) or "inline" (tests: deterministic)
    host: str = field(default=LOOPBACK_HOST, init=False)

    def __post_init__(self) -> None:
        if len(self.api_token) < MIN_TOKEN_LENGTH:
            raise ConfigError(f"API token must be at least {MIN_TOKEN_LENGTH} characters")
        if self.worker_mode not in {"thread", "inline"}:
            raise ConfigError("worker_mode must be 'thread' or 'inline'")
        if not 0 <= self.port <= 65535:
            raise ConfigError("port must be 0..65535")
        object.__setattr__(
            self, "allowed_origins", tuple(_validate_origin(o) for o in self.allowed_origins)
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "pricepilot.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"
