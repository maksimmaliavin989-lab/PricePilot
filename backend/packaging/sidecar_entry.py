"""Entry point of the bundled API sidecar (PyInstaller)."""

from pricepilot.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
