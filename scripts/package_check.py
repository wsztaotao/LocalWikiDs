"""Packaging readiness check (LW-38). Does not claim a production installer exists."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = {
        "pyinstaller_spec": (root / "localwiki.spec").exists()
        or (root / "scripts" / "localwiki.spec").exists(),
        "setup_windows_cmd": (root / "setup-windows.cmd").exists()
        or (root / "setup" / "install-work-windows.cmd").exists(),
        "entry_main": (root / "localwiki" / "__main__.py").exists(),
        "status": "BLOCKED",
        "note": "No PyInstaller build / Win11 install media in this workspace; packaging not verified.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["entry_main"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
