"""Schema layer for LocalWiki LLM Wiki (design §5.2–5.3).

Loads and validates workspace `schema/` files, falling back to packaged
`schema_templates/`. Schema version is part of the compile signature (§5.4).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Package root: localwiki/ → repo root contains schema_templates/
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES_DIR = _PACKAGE_ROOT / "schema_templates"

SCHEMA_FILENAMES: tuple[str, ...] = (
    "AGENTS.md",
    "TOOLS.md",
    "page-types.yaml",
    "lint-rules.yaml",
    "ingest-playbook.md",
)

REQUIRED_LINT_RULES: frozenset[str] = frozenset(
    {
        "format",
        "broken_links",
        "orphan_pages",
        "missing_concepts",
        "contradictions",
        "stale_content",
        "duplicates",
        "source_gaps",
        "memory_experience_consistency",
    }
)

# Core page types from §5.3 AGENTS.md (+ Memory / Persona Trait used by later phases).
REQUIRED_PAGE_TYPES: frozenset[str] = frozenset(
    {
        "Concept",
        "Topic",
        "Entity",
        "Source Summary",
        "Decision",
        "Question",
        "Memory",
        "Persona Trait",
    }
)


class SchemaValidationError(ValueError):
    """Raised when schema files are missing or structurally invalid."""


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dep path
        raise SchemaValidationError(
            "E_DEP_MISSING: PyYAML is required to parse schema YAML. "
            "Install with: pip install pyyaml"
        ) from exc
    return yaml.safe_load(text)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PageTypeSpec:
    name: str
    required_sections: tuple[str, ...] = ()
    kw_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class LintRuleSpec:
    name: str
    enabled: bool = True
    severity: str = "warning"
    frequency: str = "nightly"


@dataclass
class SchemaBundle:
    """Loaded schema for one workspace (or default templates)."""

    version: str
    agents_md: str
    tools_md: str
    ingest_playbook_md: str
    page_types: dict[str, PageTypeSpec]
    lint_rules: dict[str, LintRuleSpec]
    source_dir: Path
    file_hashes: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def content_fingerprint(self) -> str:
        """Stable hash of all schema file contents (feeds compile signature)."""
        parts = [self.file_hashes.get(name, "") for name in SCHEMA_FILENAMES]
        return _sha256_text("|".join(parts))


def schema_version_token(bundle: SchemaBundle) -> str:
    """Token written into compile signatures: version + content fingerprint."""
    return f"{bundle.version}:{bundle.content_fingerprint[:16]}"


def compile_signature(
    *,
    source_hash: str,
    extract_hash: str,
    extractor_version: str,
    schema: SchemaBundle,
    model_route_version: str = "",
    evolution_version: str = "",
    affected_page_hashes: list[str] | None = None,
) -> str:
    """Compile signature per §5.4 (schema version included).

    compile_signature = source + extract + extractor + schema + routes + evolution + pages
    """
    pages = list(affected_page_hashes or [])
    payload = {
        "source_hash": source_hash,
        "extract_hash": extract_hash,
        "extractor_version": extractor_version,
        "schema_version": schema.version,
        "schema_token": schema_version_token(schema),
        "model_route_version": model_route_version,
        "evolution_version": evolution_version,
        "affected_page_hashes": pages,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return _sha256_text(raw)


def templates_dir(override: Path | None = None) -> Path:
    return Path(override) if override is not None else DEFAULT_TEMPLATES_DIR


def _read_schema_files(directory: Path) -> dict[str, str]:
    missing = [name for name in SCHEMA_FILENAMES if not (directory / name).is_file()]
    if missing:
        raise SchemaValidationError(
            f"Schema directory incomplete at {directory}: missing {', '.join(missing)}"
        )
    return {
        name: (directory / name).read_text(encoding="utf-8") for name in SCHEMA_FILENAMES
    }


def _parse_page_types(yaml_text: str) -> tuple[str, dict[str, PageTypeSpec]]:
    data = _load_yaml(yaml_text)
    if not isinstance(data, dict):
        raise SchemaValidationError("page-types.yaml must be a mapping")
    version = str(data.get("version") or "").strip()
    if not version:
        raise SchemaValidationError("page-types.yaml missing version")
    types_raw = data.get("types")
    if not isinstance(types_raw, dict) or not types_raw:
        raise SchemaValidationError("page-types.yaml missing types mapping")
    out: dict[str, PageTypeSpec] = {}
    for name, spec in types_raw.items():
        if not isinstance(spec, dict):
            raise SchemaValidationError(f"page type {name!r} must be a mapping")
        sections = spec.get("required_sections") or []
        kw_req = spec.get("kw_required") or []
        if not isinstance(sections, list) or not all(isinstance(s, str) for s in sections):
            raise SchemaValidationError(
                f"page type {name!r}: required_sections must be a list of strings"
            )
        if not isinstance(kw_req, list) or not all(isinstance(s, str) for s in kw_req):
            raise SchemaValidationError(
                f"page type {name!r}: kw_required must be a list of strings"
            )
        out[str(name)] = PageTypeSpec(
            name=str(name),
            required_sections=tuple(sections),
            kw_required=tuple(kw_req),
        )
    return version, out


def _parse_lint_rules(yaml_text: str) -> tuple[str, dict[str, LintRuleSpec]]:
    data = _load_yaml(yaml_text)
    if not isinstance(data, dict):
        raise SchemaValidationError("lint-rules.yaml must be a mapping")
    version = str(data.get("version") or "").strip()
    if not version:
        raise SchemaValidationError("lint-rules.yaml missing version")
    rules_raw = data.get("rules")
    if not isinstance(rules_raw, dict) or not rules_raw:
        raise SchemaValidationError("lint-rules.yaml missing rules mapping")
    out: dict[str, LintRuleSpec] = {}
    for name, spec in rules_raw.items():
        if not isinstance(spec, dict):
            raise SchemaValidationError(f"lint rule {name!r} must be a mapping")
        out[str(name)] = LintRuleSpec(
            name=str(name),
            enabled=bool(spec.get("enabled", True)),
            severity=str(spec.get("severity", "warning")),
            frequency=str(spec.get("frequency", "nightly")),
        )
    return version, out


def validate_schema_contents(files: dict[str, str]) -> list[str]:
    """Validate loaded file texts; return non-fatal issues. Raise on hard errors."""
    issues: list[str] = []
    for name in SCHEMA_FILENAMES:
        if name not in files:
            raise SchemaValidationError(f"missing schema file: {name}")
        if not str(files[name]).strip():
            raise SchemaValidationError(f"schema file empty: {name}")

    pt_version, page_types = _parse_page_types(files["page-types.yaml"])
    lr_version, lint_rules = _parse_lint_rules(files["lint-rules.yaml"])

    if pt_version != lr_version:
        issues.append(
            f"version mismatch: page-types={pt_version!r} lint-rules={lr_version!r}"
        )

    missing_types = REQUIRED_PAGE_TYPES - set(page_types)
    if missing_types:
        raise SchemaValidationError(
            f"page-types.yaml missing required types: {sorted(missing_types)}"
        )

    missing_rules = REQUIRED_LINT_RULES - set(lint_rules)
    if missing_rules:
        raise SchemaValidationError(
            f"lint-rules.yaml missing required rules: {sorted(missing_rules)}"
        )

    for md_name in ("AGENTS.md", "TOOLS.md", "ingest-playbook.md"):
        if len(files[md_name].strip()) < 20:
            issues.append(f"{md_name} looks too short")

    return issues


def _build_bundle(directory: Path, files: dict[str, str]) -> SchemaBundle:
    issues = validate_schema_contents(files)
    pt_version, page_types = _parse_page_types(files["page-types.yaml"])
    lr_version, lint_rules = _parse_lint_rules(files["lint-rules.yaml"])
    version = pt_version if pt_version == lr_version else f"{pt_version}+{lr_version}"
    hashes = {name: _sha256_text(text) for name, text in files.items()}
    return SchemaBundle(
        version=version,
        agents_md=files["AGENTS.md"],
        tools_md=files["TOOLS.md"],
        ingest_playbook_md=files["ingest-playbook.md"],
        page_types=page_types,
        lint_rules=lint_rules,
        source_dir=directory.resolve(),
        file_hashes=hashes,
        issues=issues,
    )


def load_default_schema(*, templates: Path | None = None) -> SchemaBundle:
    """Load and validate packaged default schema templates."""
    directory = templates_dir(templates)
    if not directory.is_dir():
        raise SchemaValidationError(f"schema templates directory not found: {directory}")
    files = _read_schema_files(directory)
    return _build_bundle(directory, files)


def ensure_workspace_schema(
    workspace: Path,
    *,
    templates: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Copy default templates into `<workspace>/schema/` if missing.

    Never overwrites existing files unless ``overwrite=True``.
    Returns the schema directory path.
    """
    workspace = Path(workspace)
    schema_dir = workspace / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    src = templates_dir(templates)
    for name in SCHEMA_FILENAMES:
        dest = schema_dir / name
        if dest.exists() and not overwrite:
            continue
        text = (src / name).read_text(encoding="utf-8")
        dest.write_text(text, encoding="utf-8", newline="\n")
    return schema_dir


def load_workspace_schema(
    workspace: Path,
    *,
    templates: Path | None = None,
    ensure_defaults: bool = True,
) -> SchemaBundle:
    """Load schema from workspace, optionally seeding from default templates."""
    workspace = Path(workspace)
    schema_dir = workspace / "schema"
    if ensure_defaults:
        ensure_workspace_schema(workspace, templates=templates, overwrite=False)
    if not schema_dir.is_dir():
        raise SchemaValidationError(f"workspace schema directory missing: {schema_dir}")
    files = _read_schema_files(schema_dir)
    return _build_bundle(schema_dir, files)
