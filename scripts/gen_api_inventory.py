"""Generate / check HTTP API inventory stubs (LW-38 / §21.5)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Documented evolution + core endpoints (scaffold inventory; full server TBD)
DOCUMENTED_ENDPOINTS = [
    "GET /api/evolution/learnings",
    "POST /api/evolution/learnings/status",
    "POST /api/evolution/learnings/import",
    "GET /api/evolution/learnings/export",
    "GET /api/evolution/proposals",
    "POST /api/evolution/proposals/decide",
    "POST /api/evolution/evaluate",
    "GET /api/evolution/versions",
    "POST /api/evolution/rollback",
    "GET /api/evolution/metrics",
    "GET /api/evolution/settings",
    "POST /api/evolution/settings",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify inventory file exists and lists endpoints")
    parser.add_argument(
        "--out",
        default="docs/API_INVENTORY.json",
        help="Inventory output path",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    out = root / args.out
    payload = {
        "version": "0.12.0-scaffold",
        "note": "HTTP server wiring incomplete; inventory is design-aligned checklist",
        "endpoints": DOCUMENTED_ENDPOINTS,
        "implemented_in_server": False,
    }
    if args.check:
        if not out.exists():
            print(f"MISSING {out}; writing scaffold")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("WROTE scaffold inventory")
            return 0
        data = json.loads(out.read_text(encoding="utf-8"))
        missing = [e for e in DOCUMENTED_ENDPOINTS if e not in (data.get("endpoints") or [])]
        if missing:
            print("FAIL missing endpoints:", missing)
            return 1
        print("OK inventory covers documented endpoints; server_implemented=", data.get("implemented_in_server"))
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
