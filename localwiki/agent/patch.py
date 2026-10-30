"""apply_patch parser and applier (design §11.7 / AT-AGT-03).

Codex-compatible ``*** Begin Patch`` format. Paths must stay inside allowed
roots; ``wiki/`` targets become ChangeSet ops (never direct writes).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Sequence

PatchOpKind = Literal["add", "update", "delete", "move"]


@dataclass
class Hunk:
    """One @@ context block with -/+ lines."""

    header: str
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "header": self.header,
            "old_lines": list(self.old_lines),
            "new_lines": list(self.new_lines),
        }


@dataclass
class PatchOp:
    kind: PatchOpKind
    path: str
    move_to: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    add_content: str | None = None  # for add: full new file text from + lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "move_to": self.move_to,
            "hunks": [h.to_dict() for h in self.hunks],
            "add_content": self.add_content,
        }


@dataclass
class ChangeSetOp:
    """Minimal ChangeSet page operation (CommitService not yet present)."""

    op: str  # create | update | delete | rename
    path: str
    base_sha256: str | None = None
    content: str | None = None
    move_to: str | None = None
    reason: str = "apply_patch_wiki_target"

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "path": self.path,
            "base_sha256": self.base_sha256,
            "content": self.content,
            "move_to": self.move_to,
            "reason": self.reason,
        }


@dataclass
class ChangeSetDraft:
    changeset_id: str
    status: str = "draft"
    ops: list[ChangeSetOp] = field(default_factory=list)
    source: str = "apply_patch"

    def to_dict(self) -> dict[str, Any]:
        return {
            "changeset_id": self.changeset_id,
            "status": self.status,
            "ops": [o.to_dict() for o in self.ops],
            "source": self.source,
        }


@dataclass
class ApplyPatchResult:
    ok: bool
    applied: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    changesets: list[ChangeSetDraft] = field(default_factory=list)
    rejected_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "applied": list(self.applied),
            "errors": list(self.errors),
            "changesets": [c.to_dict() for c in self.changesets],
            "rejected_paths": list(self.rejected_paths),
        }


_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = re.compile(r"^\*\*\* Add File:\s*(.+)$")
_UPDATE = re.compile(r"^\*\*\* Update File:\s*(.+)$")
_DELETE = re.compile(r"^\*\*\* Delete File:\s*(.+)$")
_MOVE = re.compile(r"^\*\*\* Move to:\s*(.+)$")
_HUNK = re.compile(r"^@@(.*)$")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_patch(text: str) -> list[PatchOp]:
    """Parse Codex-style patch into PatchOp list. Raises ValueError on structural errors."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not any(ln.strip() == _BEGIN for ln in lines):
        raise ValueError("missing *** Begin Patch")
    if not any(ln.strip() == _END for ln in lines):
        raise ValueError("missing *** End Patch")

    ops: list[PatchOp] = []
    i = 0
    in_patch = False
    current: PatchOp | None = None
    collecting_add: list[str] = []

    def flush_add() -> None:
        nonlocal current, collecting_add
        if current and current.kind == "add":
            # strip leading '+' markers
            body_lines = []
            for raw in collecting_add:
                if raw.startswith("+"):
                    body_lines.append(raw[1:])
                elif raw.startswith("\\"):
                    continue
                else:
                    body_lines.append(raw)
            current.add_content = "\n".join(body_lines)
            if current.add_content and not current.add_content.endswith("\n"):
                # keep as-is; files may omit trailing newline
                pass
            collecting_add = []

    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if stripped == _BEGIN:
            in_patch = True
            i += 1
            continue
        if stripped == _END:
            flush_add()
            if current:
                ops.append(current)
                current = None
            in_patch = False
            i += 1
            continue
        if not in_patch:
            i += 1
            continue

        m_add = _ADD.match(stripped)
        m_upd = _UPDATE.match(stripped)
        m_del = _DELETE.match(stripped)
        m_move = _MOVE.match(stripped)

        if m_add or m_upd or m_del:
            flush_add()
            if current:
                ops.append(current)
            if m_add:
                current = PatchOp(kind="add", path=m_add.group(1).strip())
                collecting_add = []
            elif m_upd:
                current = PatchOp(kind="update", path=m_upd.group(1).strip())
            else:
                current = PatchOp(kind="delete", path=m_del.group(1).strip())
            i += 1
            continue

        if m_move and current and current.kind == "update":
            # Codex: Move to after Update File means rename
            current.kind = "move"
            current.move_to = m_move.group(1).strip()
            i += 1
            continue

        if current is None:
            i += 1
            continue

        if current.kind == "add":
            collecting_add.append(ln)
            i += 1
            continue

        if current.kind in ("update", "move") and _HUNK.match(stripped):
            hunk = Hunk(header=stripped)
            i += 1
            while i < len(lines):
                row = lines[i]
                rs = row.strip()
                if (
                    rs.startswith("*** ")
                    or rs == _END
                    or _HUNK.match(rs)
                ):
                    break
                if row.startswith("-"):
                    hunk.old_lines.append(row[1:])
                elif row.startswith("+"):
                    hunk.new_lines.append(row[1:])
                elif row.startswith(" "):
                    # context line present in both
                    hunk.old_lines.append(row[1:])
                    hunk.new_lines.append(row[1:])
                elif row.startswith("\\"):
                    pass
                else:
                    # treat as context without marker (lenient)
                    hunk.old_lines.append(row)
                    hunk.new_lines.append(row)
                i += 1
            current.hunks.append(hunk)
            continue

        i += 1

    flush_add()
    if current:
        ops.append(current)
    return ops


def normalize_rel(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def is_wiki_path(path: str) -> bool:
    p = normalize_rel(path).lower()
    return p == "wiki" or p.startswith("wiki/")


def validate_path(
    path: str,
    *,
    allowed_roots: Sequence[str],
    workspace_root: Path | None = None,
) -> str | None:
    """Return error message if path is illegal, else None."""
    raw = path.strip()
    if not raw:
        return "empty_path"
    norm = normalize_rel(raw)
    if re.match(r"^[a-zA-Z]:[\\/]", raw) or raw.startswith("/") or raw.startswith("\\\\"):
        return "absolute_path_rejected"
    parts = norm.split("/")
    if ".." in parts:
        return "path_traversal_rejected"
    if any(p in ("", ".") for p in parts if p == ""):
        return "empty_path_segment"

    # Must be under one of allowed roots (prefix)
    roots = [normalize_rel(r).rstrip("/") for r in allowed_roots]
    ok = False
    for root in roots:
        if norm == root or norm.startswith(root + "/"):
            ok = True
            break
    if not ok:
        return f"path_outside_allowed_roots:{norm}"

    if workspace_root is not None:
        target = (workspace_root / norm).resolve()
        try:
            target.relative_to(workspace_root.resolve())
        except ValueError:
            return "symlink_or_resolve_escape"
        # Symlink escape: if any parent is symlink pointing outside
        cur = target
        while True:
            if cur.is_symlink():
                resolved = cur.resolve()
                try:
                    resolved.relative_to(workspace_root.resolve())
                except ValueError:
                    return "symlink_escape"
            if cur == workspace_root.resolve() or cur.parent == cur:
                break
            cur = cur.parent

    return None


def apply_hunks_to_text(original: str, hunks: Sequence[Hunk]) -> tuple[str | None, str | None]:
    """Apply hunks with exact context match. Returns (new_text, error)."""
    text = original.replace("\r\n", "\n").replace("\r", "\n")
    # Work line-oriented without forcing trailing newline semantics awkwardly
    ends_with_nl = text.endswith("\n")
    lines = text.split("\n")
    if ends_with_nl and lines and lines[-1] == "":
        lines = lines[:-1]

    for hunk in hunks:
        old = hunk.old_lines
        new = hunk.new_lines
        if not old:
            # pure insert at end if no context
            lines.extend(new)
            continue
        # Find exact contiguous match
        idx = _find_subsequence(lines, old)
        if idx is None:
            preview = "\n".join(old[:3])
            return None, f"context_mismatch:{hunk.header}:{preview}"
        lines[idx : idx + len(old)] = list(new)

    out = "\n".join(lines)
    if ends_with_nl:
        out += "\n"
    return out, None


def _find_subsequence(hay: list[str], needle: list[str]) -> int | None:
    if not needle:
        return 0
    n = len(needle)
    for i in range(0, len(hay) - n + 1):
        if hay[i : i + n] == needle:
            return i
    return None


DEFAULT_ALLOWED_ROOTS = ("outputs", "wiki", "workspace", "projects")


class PatchApplier:
    """Apply parsed ops to a workspace directory or emit ChangeSets for wiki/."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        allowed_roots: Sequence[str] | None = None,
    ) -> None:
        self.root = Path(workspace_root)
        self.allowed_roots = list(allowed_roots or DEFAULT_ALLOWED_ROOTS)

    def apply_text(self, patch_text: str) -> ApplyPatchResult:
        try:
            ops = parse_patch(patch_text)
        except ValueError as exc:
            return ApplyPatchResult(ok=False, errors=[str(exc)])
        return self.apply_ops(ops)

    def apply_ops(self, ops: Sequence[PatchOp]) -> ApplyPatchResult:
        applied: list[dict[str, Any]] = []
        errors: list[str] = []
        rejected: list[str] = []
        changesets: list[ChangeSetDraft] = []
        wiki_ops: list[ChangeSetOp] = []

        for op in ops:
            err = validate_path(
                op.path,
                allowed_roots=self.allowed_roots,
                workspace_root=self.root,
            )
            if err:
                rejected.append(op.path)
                errors.append(f"{op.path}:{err}")
                continue
            if op.move_to:
                err2 = validate_path(
                    op.move_to,
                    allowed_roots=self.allowed_roots,
                    workspace_root=self.root,
                )
                if err2:
                    rejected.append(op.move_to)
                    errors.append(f"{op.move_to}:{err2}")
                    continue

            if is_wiki_path(op.path) or (op.move_to and is_wiki_path(op.move_to)):
                cs_op, cs_err = self._to_changeset_op(op)
                if cs_err:
                    errors.append(cs_err)
                    continue
                assert cs_op is not None
                wiki_ops.append(cs_op)
                applied.append(
                    {
                        "kind": op.kind,
                        "path": op.path,
                        "via": "changeset",
                        "changeset_op": cs_op.to_dict(),
                    }
                )
                continue

            # Direct filesystem apply outside wiki/
            try:
                info = self._apply_fs(op)
                applied.append(info)
            except FileNotFoundError as exc:
                errors.append(f"{op.path}:file_not_found:{exc}")
            except ValueError as exc:
                errors.append(str(exc))
            except OSError as exc:
                errors.append(f"{op.path}:os_error:{exc}")

        if wiki_ops:
            changesets.append(
                ChangeSetDraft(
                    changeset_id=f"cs_{uuid.uuid4().hex[:12]}",
                    status="draft",
                    ops=wiki_ops,
                )
            )

        return ApplyPatchResult(
            ok=not errors and bool(applied),
            applied=applied,
            errors=errors,
            changesets=changesets,
            rejected_paths=rejected,
        )

    def _to_changeset_op(self, op: PatchOp) -> tuple[ChangeSetOp | None, str | None]:
        rel = normalize_rel(op.path)
        target = self.root / rel
        base_sha: str | None = None
        content: str | None = None

        if op.kind == "add":
            content = op.add_content or ""
            return (
                ChangeSetOp(
                    op="create",
                    path=rel,
                    base_sha256=None,
                    content=content,
                ),
                None,
            )

        if op.kind == "delete":
            if target.is_file():
                base_sha = sha256_text(target.read_text(encoding="utf-8"))
            return (
                ChangeSetOp(op="delete", path=rel, base_sha256=base_sha),
                None,
            )

        if op.kind == "move":
            if target.is_file():
                base_sha = sha256_text(target.read_text(encoding="utf-8"))
                content = target.read_text(encoding="utf-8")
            return (
                ChangeSetOp(
                    op="rename",
                    path=rel,
                    move_to=normalize_rel(op.move_to or ""),
                    base_sha256=base_sha,
                    content=content,
                ),
                None,
            )

        # update
        if not target.is_file():
            return None, f"{rel}:wiki_update_missing_file"
        original = target.read_text(encoding="utf-8")
        base_sha = sha256_text(original)
        new_text, err = apply_hunks_to_text(original, op.hunks)
        if err:
            return None, f"{rel}:{err}"
        return (
            ChangeSetOp(
                op="update",
                path=rel,
                base_sha256=base_sha,
                content=new_text,
            ),
            None,
        )

    def _apply_fs(self, op: PatchOp) -> dict[str, Any]:
        rel = normalize_rel(op.path)
        target = self.root / rel

        if op.kind == "add":
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError(f"{rel}:add_file_exists")
            text = op.add_content or ""
            target.write_text(text, encoding="utf-8")
            return {"kind": "add", "path": rel, "via": "filesystem", "bytes": len(text.encode())}

        if op.kind == "delete":
            if not target.exists():
                raise FileNotFoundError(rel)
            target.unlink()
            return {"kind": "delete", "path": rel, "via": "filesystem", "risk": "R3"}

        if op.kind == "move":
            dest = self.root / normalize_rel(op.move_to or "")
            if not target.exists():
                raise FileNotFoundError(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            target.replace(dest)
            return {
                "kind": "move",
                "path": rel,
                "move_to": normalize_rel(op.move_to or ""),
                "via": "filesystem",
            }

        # update
        if not target.is_file():
            raise FileNotFoundError(rel)
        original = target.read_text(encoding="utf-8")
        new_text, err = apply_hunks_to_text(original, op.hunks)
        if err:
            raise ValueError(f"{rel}:{err}")
        assert new_text is not None
        target.write_text(new_text, encoding="utf-8")
        return {
            "kind": "update",
            "path": rel,
            "via": "filesystem",
            "base_sha256": sha256_text(original),
            "new_sha256": sha256_text(new_text),
        }


def apply_patch(
    patch_text: str,
    workspace_root: Path | str,
    *,
    allowed_roots: Sequence[str] | None = None,
) -> ApplyPatchResult:
    return PatchApplier(workspace_root, allowed_roots=allowed_roots).apply_text(patch_text)
