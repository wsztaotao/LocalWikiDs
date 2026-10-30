"""Tests for scripts/migrate_0_11_to_0_12.py (LW-02)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_0_11_to_0_12 as mig  # noqa: E402


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, text: str | bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text if isinstance(text, bytes) else text.encode("utf-8")
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def test_migrate_creates_new_dirs_and_defaults(tmp_path: Path) -> None:
    ws = tmp_path / "LocalWikiData"
    # Simulate v0.11 workspace with existing user content.
    old_wiki = ws / "wiki" / "concepts" / "alpha.md"
    old_hash = _write(old_wiki, "# Alpha\n\ntype: Concept\n")
    old_cfg = ws / ".kw" / "config.json"
    cfg_hash = _write(old_cfg, '{"workspace_uid": "test-uid"}\n')
    index_sqlite = ws / ".kw" / "cache" / "index.sqlite"
    idx_hash = _write(index_sqlite, b"sqlite-placeholder")

    before = mig.hash_existing_files(ws)
    report = mig.migrate(ws, dry_run=False)
    after = mig.hash_existing_files(ws)

    assert report.created_dirs
    assert report.created_files
    # Pre-existing hashes unchanged.
    for rel, digest in before.items():
        assert after[rel] == digest

    assert (ws / "schema" / "AGENTS.md").is_file()
    assert (ws / "schema" / "TOOLS.md").is_file()
    assert (ws / "schema" / "page-types.yaml").is_file()
    assert (ws / "schema" / "lint-rules.yaml").is_file()
    assert (ws / "schema" / "ingest-playbook.md").is_file()
    assert (ws / "raw" / "chats").is_dir()
    assert (ws / "raw" / "agent-sessions").is_dir()
    assert (ws / "raw" / "behavior").is_dir()
    for kind in ("semantic", "procedural", "preference", "relational"):
        assert (ws / "wiki" / "memories" / kind).is_dir()
    assert (ws / "wiki" / "profiles" / "style").is_dir()
    assert (ws / ".kw" / "skills").is_dir()
    assert (ws / ".kw" / "skills" / "registry.json").is_file()
    assert (ws / ".kw" / "memory" / "episodes").is_dir()
    assert (ws / ".kw" / "learnings" / "LEARNINGS.md").is_file()
    assert (ws / ".kw" / "evolution" / "params.json").is_file()
    assert (ws / ".kw" / "evolution" / "log.md").is_file()
    assert (ws / ".kw" / "cache" / "vectors.sqlite").is_file()
    assert (ws / ".kw" / "model-routes.json").is_file()

    routes = json.loads((ws / ".kw" / "model-routes.json").read_text(encoding="utf-8"))
    assert routes["version"] == 2
    assert len(routes["routes"]) == 13

    assert mig.file_sha256(old_wiki) == old_hash
    assert mig.file_sha256(old_cfg) == cfg_hash
    assert mig.file_sha256(index_sqlite) == idx_hash


def test_migrate_does_not_overwrite_existing_defaults(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    custom = "# custom agents\n"
    custom_hash = _write(ws / "schema" / "AGENTS.md", custom)
    report = mig.migrate(ws, dry_run=False)
    assert "schema/AGENTS.md" in report.skipped_existing
    assert (ws / "schema" / "AGENTS.md").read_text(encoding="utf-8") == custom
    assert mig.file_sha256(ws / "schema" / "AGENTS.md") == custom_hash


def test_dry_run_creates_nothing(tmp_path: Path) -> None:
    ws = tmp_path / "dry"
    ws.mkdir()
    _write(ws / "wiki" / "index.md", "# Index\n")
    before = mig.hash_existing_files(ws)
    report = mig.migrate(ws, dry_run=True)
    after = mig.hash_existing_files(ws)
    assert before == after
    assert not (ws / "schema").exists()
    assert report.dry_run is True
    assert any(a.kind == "mkdir" for a in report.actions)
    assert any(a.kind == "write_file" for a in report.actions)


def test_cli_json_and_hash_guard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = tmp_path / "cli-ws"
    _write(ws / "wiki" / "topics" / "t.md", "hello\n")
    rc = mig.main([str(ws), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["preexisting_hashes_unchanged"] is True
    assert payload["created_files"]
    assert (ws / "schema" / "AGENTS.md").is_file()


def test_idempotent_second_run(tmp_path: Path) -> None:
    ws = tmp_path / "idem"
    mig.migrate(ws, dry_run=False)
    after_first = mig.hash_existing_files(ws)
    report2 = mig.migrate(ws, dry_run=False)
    after_second = mig.hash_existing_files(ws)
    assert after_first == after_second
    assert report2.created_files == []
    assert "schema/AGENTS.md" in report2.skipped_existing
