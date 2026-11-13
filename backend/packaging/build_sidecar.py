"""Build the API sidecar binary that Tauri bundles via ``bundle.externalBin``.

Output: ``apps/desktop/src-tauri/binaries/pricepilot-api-<rust target triple>[.exe]``.
Run: ``uv run python packaging/build_sidecar.py`` from ``backend/``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
BINARIES = ROOT / "apps" / "desktop" / "src-tauri" / "binaries"
MIGRATIONS = BACKEND / "src" / "pricepilot" / "infrastructure" / "db" / "migrations"
OCR_MODELS = BACKEND / "src" / "pricepilot" / "dip" / "ocr" / "models"


def rapidocr_data(sep: str) -> list[str]:
    """RapidOCR reads its YAML configuration from its package directory. Only the YAML files
    are shipped: the wheel's own Chinese models (~31 MB) are not used — PricePilot loads its
    bundled models by explicit path."""
    import rapidocr

    root = Path(rapidocr.__file__).parent
    args: list[str] = []
    for name in ("config.yaml", "default_models.yaml"):
        args += ["--add-data", f"{root / name}{sep}rapidocr"]
    return args


def target_triple() -> str:
    out = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("cannot determine the Rust host triple")


def main() -> int:
    triple = target_triple()
    work = BACKEND / "build" / "sidecar"
    shutil.rmtree(work, ignore_errors=True)
    sep = ";" if sys.platform == "win32" else ":"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "pricepilot-api",
            "--distpath",
            str(work / "dist"),
            "--workpath",
            str(work / "build"),
            "--specpath",
            str(work),
            # Alembic loads revision files from disk, so they ship as data, not only bytecode.
            "--add-data",
            f"{MIGRATIONS}{sep}pricepilot/infrastructure/db/migrations",
            # OCR models (Apache-2.0, see NOTICE.md) ship as data next to the engine code.
            "--add-data",
            f"{OCR_MODELS}{sep}pricepilot/dip/ocr/models",
            *rapidocr_data(sep),
            "--collect-submodules",
            "rapidocr",
            "--collect-submodules",
            "uvicorn",
            "--collect-submodules",
            "pricepilot",
            "--hidden-import",
            "defusedxml",
            # Local mode is SQLite-only; the PostgreSQL driver is a dev/cloud extra.
            "--exclude-module",
            "psycopg",
            "--exclude-module",
            "psycopg_binary",
            "--exclude-module",
            "pytest",
            # Inference back-ends RapidOCR can use but PricePilot does not (onnxruntime only).
            *[
                arg
                for module in ("torch", "paddle", "openvino", "tensorrt", "matplotlib", "tkinter")
                for arg in ("--exclude-module", module)
            ],
            str(BACKEND / "packaging" / "sidecar_entry.py"),
        ],
        cwd=BACKEND,
        check=True,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    built = work / "dist" / f"pricepilot-api{suffix}"
    BINARIES.mkdir(parents=True, exist_ok=True)
    target = BINARIES / f"pricepilot-api-{triple}{suffix}"
    shutil.copy2(built, target)
    print(f"sidecar: {target.relative_to(ROOT)} ({target.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
