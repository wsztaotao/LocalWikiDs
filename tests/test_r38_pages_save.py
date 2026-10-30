"""R3.8 POST /api/pages/content via CommitService."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.core import CommitService  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_pages_save_create_update(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    svc = CommitService(ws)
    # mimic server handler logic
    page_path = "notes/hello.md"
    content = "# Hello\n"
    wiki = ws / "wiki"
    target = wiki / page_path
    from localwiki.core import file_sha256

    op = {"op": "create", "path": page_path, "content": content}
    cs = svc.create_changeset(purpose="ui save", ops=[op], state="ready")
    svc.accept(cs.change_uid)
    assert target.is_file()
    op2 = {
        "op": "update",
        "path": page_path,
        "content": "# Hello2\n",
        "base_sha256": file_sha256(target),
    }
    cs2 = svc.create_changeset(purpose="ui save2", ops=[op2], state="ready")
    svc.accept(cs2.change_uid)
    assert "Hello2" in target.read_text(encoding="utf-8")
