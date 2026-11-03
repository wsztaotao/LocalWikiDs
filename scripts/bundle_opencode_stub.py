#!/usr/bin/env python3
"""Copy a Fake OpenCode bin into runtime/opencode/ (packaging draft / tests)."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def bundle(dest_root: Path, *, fixture: Path | None = None) -> Path:
    dest = dest_root / "runtime" / "opencode"
    dest.mkdir(parents=True, exist_ok=True)
    if fixture and fixture.is_file():
        target = dest / ("opencode.exe" if os.name == "nt" else "opencode")
        shutil.copy2(fixture, target)
        if os.name != "nt":
            target.chmod(target.stat().st_mode | 0o111)
        return target

    # Write a tiny Fake script that echoes args
    if os.name == "nt":
        target = dest / "opencode.cmd"
        target.write_text(
            "@echo off\r\necho fake-opencode %*\r\nexit /b 0\r\n",
            encoding="utf-8",
        )
        # Also write .exe-named py launcher for resolve_bin which looks for opencode.exe
        py_fake = dest / "opencode.exe"
        # On Windows tests we use a .bat renamed — resolve looks for opencode.exe;
        # provide a PowerShell-friendly cmd copy as opencode.exe is not runnable without PE.
        # Instead drop a python script and set LOCALWIKI in tests.
        shim = dest / "opencode"
        shim.write_text(
            "#!/usr/bin/env python\nimport sys\nprint('fake-opencode', *sys.argv[1:])\nsys.exit(0)\n",
            encoding="utf-8",
        )
        return shim
    target = dest / "opencode"
    target.write_text(
        "#!/usr/bin/env python3\nimport sys\nprint('fake-opencode', *sys.argv[1:])\nsys.exit(0)\n",
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | 0o111)
    return target


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bundle stub OpenCode into runtime/opencode")
    p.add_argument("--dest", default=".", help="Destination root (repo or workspace)")
    p.add_argument("--fixture", default=None, help="Optional real/fixture binary to copy")
    args = p.parse_args(argv)
    path = bundle(Path(args.dest), fixture=Path(args.fixture) if args.fixture else None)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
