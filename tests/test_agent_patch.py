"""AT-AGT-03: apply_patch path bounds, context mismatch, wiki→ChangeSet (LW-35)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.agent.patch import (  # noqa: E402
    apply_patch,
    is_wiki_path,
    parse_patch,
    validate_path,
)


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "outputs").mkdir()
    (tmp_path / "wiki").mkdir()
    return tmp_path


def test_parse_add_update_delete() -> None:
    text = """*** Begin Patch
*** Update File: outputs/a.md
@@ header
- old
+ new
*** Add File: outputs/b.md
+# hello
*** Delete File: outputs/c.md
*** End Patch
"""
    ops = parse_patch(text)
    assert [o.kind for o in ops] == ["update", "add", "delete"]
    assert ops[1].add_content == "# hello"


def test_update_file_happy_path(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    target = root / "outputs" / "plan.md"
    target.write_text("## 实施计划\n- 第一阶段：需求确认\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: outputs/plan.md
@@ ## 实施计划
-## 实施计划
-- 第一阶段：需求确认
+## 实施计划
+- 第一阶段：需求确认与验收标准对齐
*** End Patch
"""
    result = apply_patch(patch, root)
    assert result.ok, result.errors
    assert "验收标准" in target.read_text(encoding="utf-8")


def test_context_mismatch_returns_clear_error(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    (root / "outputs" / "a.md").write_text("alpha\n", encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: outputs/a.md
@@
- beta
+ gamma
*** End Patch
"""
    result = apply_patch(patch, root)
    assert not result.ok
    assert any("context_mismatch" in e for e in result.errors)


def test_path_traversal_rejected(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    patch = """*** Begin Patch
*** Add File: outputs/../../secret.txt
+leak
*** End Patch
"""
    result = apply_patch(patch, root)
    assert not result.ok
    assert result.rejected_paths
    assert any("path_traversal" in e for e in result.errors)


def test_absolute_path_rejected(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    patch = """*** Begin Patch
*** Add File: C:/Windows/temp.txt
+x
*** End Patch
"""
    result = apply_patch(patch, root)
    assert not result.ok
    assert any("absolute_path" in e for e in result.errors)


def test_outside_allowed_roots_rejected(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    patch = """*** Begin Patch
*** Add File: etc/passwd
+x
*** End Patch
"""
    result = apply_patch(patch, root)
    assert not result.ok
    assert any("path_outside_allowed_roots" in e for e in result.errors)


def test_wiki_write_becomes_changeset_not_direct(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    page = root / "wiki" / "Topic.md"
    page.write_text("# Topic\nbody\n", encoding="utf-8")
    before = page.read_text(encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: wiki/Topic.md
@@
-# Topic
-body
+# Topic
+body updated
*** End Patch
"""
    result = apply_patch(patch, root)
    assert result.ok, result.errors
    assert page.read_text(encoding="utf-8") == before  # no direct write
    assert len(result.changesets) == 1
    cs = result.changesets[0]
    assert cs.status == "draft"
    assert cs.ops[0].op == "update"
    assert cs.ops[0].base_sha256
    assert "body updated" in (cs.ops[0].content or "")
    assert result.applied[0]["via"] == "changeset"


def test_wiki_add_changeset(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    patch = """*** Begin Patch
*** Add File: wiki/New.md
+# New
*** End Patch
"""
    result = apply_patch(patch, root)
    assert result.ok
    assert not (root / "wiki" / "New.md").exists()
    assert result.changesets[0].ops[0].op == "create"


def test_delete_file_op_parsed_as_delete() -> None:
    ops = parse_patch(
        "*** Begin Patch\n*** Delete File: outputs/x.md\n*** End Patch\n"
    )
    assert ops[0].kind == "delete"


def test_validate_path_helpers() -> None:
    assert is_wiki_path("wiki/a.md")
    assert validate_path("outputs/a.md", allowed_roots=["outputs"]) is None
    assert validate_path("../x", allowed_roots=["outputs"]) == "path_traversal_rejected"
