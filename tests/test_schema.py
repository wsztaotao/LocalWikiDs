"""Tests for localwiki.llmwiki.schema (LW-04)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.llmwiki.schema import (  # noqa: E402
    SCHEMA_FILENAMES,
    SchemaValidationError,
    compile_signature,
    ensure_workspace_schema,
    load_default_schema,
    load_workspace_schema,
    schema_version_token,
)


def test_load_default_templates() -> None:
    bundle = load_default_schema()
    assert bundle.version == "0.12.0"
    assert "维护规则" in bundle.agents_md
    assert "CommitService" in bundle.tools_md
    assert "Concept" in bundle.page_types
    assert "format" in bundle.lint_rules
    assert len(bundle.lint_rules) >= 9
    assert all(name in bundle.file_hashes for name in SCHEMA_FILENAMES)
    assert len(schema_version_token(bundle)) > 10


def test_workspace_seed_and_load(tmp_path: Path) -> None:
    ws = tmp_path / "LocalWikiData"
    schema_dir = ensure_workspace_schema(ws)
    for name in SCHEMA_FILENAMES:
        assert (schema_dir / name).is_file()

    bundle = load_workspace_schema(ws, ensure_defaults=False)
    assert bundle.version == "0.12.0"
    assert bundle.source_dir == schema_dir.resolve()


def test_ensure_does_not_overwrite(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace_schema(ws)
    custom = "# custom agents\n\ncustom body for length check ok\n"
    agents = ws / "schema" / "AGENTS.md"
    agents.write_text(custom, encoding="utf-8")
    ensure_workspace_schema(ws)
    assert agents.read_text(encoding="utf-8") == custom


def test_compile_signature_includes_schema_version(tmp_path: Path) -> None:
    from localwiki.llmwiki.schema import _build_bundle

    bundle = load_default_schema()
    sig1 = compile_signature(
        source_hash="src1",
        extract_hash="ext1",
        extractor_version="1.0",
        schema=bundle,
        model_route_version="2",
        evolution_version="e0",
    )
    sig1b = compile_signature(
        source_hash="src1",
        extract_hash="ext1",
        extractor_version="1.0",
        schema=bundle,
        model_route_version="2",
        evolution_version="e0",
    )
    assert sig1 == sig1b
    assert len(sig1) == 64
    assert schema_version_token(bundle).startswith("0.12.0:")

    files = {
        name: (bundle.source_dir / name).read_text(encoding="utf-8")
        for name in SCHEMA_FILENAMES
    }
    files["AGENTS.md"] = files["AGENTS.md"] + "\n# changed\n"
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    mutated = _build_bundle(tmp_path, files)
    assert mutated.content_fingerprint != bundle.content_fingerprint
    sig2 = compile_signature(
        source_hash="src1",
        extract_hash="ext1",
        extractor_version="1.0",
        schema=mutated,
        model_route_version="2",
        evolution_version="e0",
    )
    assert sig2 != sig1


def test_invalid_page_types_rejected(tmp_path: Path) -> None:
    src = load_default_schema().source_dir
    for name in SCHEMA_FILENAMES:
        (tmp_path / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "page-types.yaml").write_text(
        "version: '0.12.0'\ntypes: {}\n", encoding="utf-8"
    )
    with pytest.raises(SchemaValidationError, match="types"):
        from localwiki.llmwiki.schema import _build_bundle, _read_schema_files

        files = _read_schema_files(tmp_path)
        # page-types empty types fails earlier in parse
        _build_bundle(tmp_path, files)


def test_missing_lint_rule_rejected(tmp_path: Path) -> None:
    src = load_default_schema().source_dir
    for name in SCHEMA_FILENAMES:
        (tmp_path / name).write_text(
            (src / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "lint-rules.yaml").write_text(
        "version: '0.12.0'\nrules:\n  format:\n    enabled: true\n    severity: error\n",
        encoding="utf-8",
    )
    with pytest.raises(SchemaValidationError, match="missing required rules"):
        from localwiki.llmwiki.schema import _build_bundle, _read_schema_files

        _build_bundle(tmp_path, _read_schema_files(tmp_path))
