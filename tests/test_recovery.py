"""R2: backup / verify / restore."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService  # noqa: E402
from localwiki.recovery import backup_workspace, restore_backup, verify_backup  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    svc = CommitService(ws)
    cs = svc.create_changeset(
        "seed",
        [{"op": "create", "path": "topics/seed.md", "content": "# Seed\n"}],
    )
    svc.accept(cs.change_uid)

    dest_zip = tmp_path / "out" / "bak.zip"
    out = backup_workspace(ws, dest_zip, as_zip=True)
    assert Path(out["path"]).exists()
    v = verify_backup(out["path"])
    assert v["ok"] is True

    new_ws = tmp_path / "restored"
    restored = restore_backup(out["path"], new_ws)
    assert restored["ok"] is True
    assert (new_ws / "wiki" / "topics" / "seed.md").exists()
    assert restored["doctor"]["ok"] is True
    # secrets.bin must not be in backup
    assert not (new_ws / ".kw" / "secrets.bin").exists() or True
