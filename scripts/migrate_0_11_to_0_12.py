#!/usr/bin/env python3
"""Migrate a LocalWiki workspace from v0.11 layout to v0.12 layout.

Only creates missing directories and default files. Never moves, renames, or
overwrites existing user data. Existing file contents are left untouched.

Usage:
  python scripts/migrate_0_11_to_0_12.py <workspace> [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = "0.12.0"

DEFAULT_AGENTS_MD = """# LocalWiki 维护规则

## 你的角色
你是这个知识库的维护者。原始资料只读；Wiki 页面通过修改提案写入。

## 页面类型
- Concept：一个概念一页，含定义、适用条件、证据、关联、未解决问题。
- Topic：跨来源综合，必须列出各来源的一致点与分歧。
- Entity：人物、机构、项目、系统。
- Source Summary：一份资料说了什么，放在 references/。
- Decision：用户明确做出的决定，记录决定者、日期、范围。
- Question：待解决的问题。
- Memory：已确认的长期记忆。
- Persona Trait：工作风格维度。

## Ingest 规则
1. 先读 index.md，找出可能受影响的页面。
2. 更新已有页面优先于新建页面；新建前检查标题和别名是否重复。
3. 每个事实性陈述都要有证据定位（locator），不得编造。
4. 新信息与已有结论冲突时，保留双方并标记冲突，不得自行裁决。
5. 一次 Ingest 修改的页面数上限：15；超出时拆成多批并说明原因。

## Query 规则
- 先查 Wiki，必要时回到原始证据核对。
- 区分“来源事实 / 综合分析 / 模型推测”。
- 有长期价值的回答，提出保存为 Topic 页的建议。

## 写作约定
- 中文为主，术语首次出现时附英文。
- 链接使用标准 Markdown 相对链接。

## 个人化
- 写作风格遵循系统下发的 StyleDirectives，不在本文件中重复。
"""

DEFAULT_TOOLS_MD = """# LocalWiki 工具约定

本文件描述智能体可用工具的本地约定。具体 ToolSpec 以运行时注册表为准。

## 原则
- 正式写入 `wiki/` 必须经 CommitService，不得直接改文件。
- 外部写入与联网默认关闭，需用户授权。
- Skill / MCP / Composio 调用遵守权限清单与审批模式。
"""

DEFAULT_PAGE_TYPES_YAML = """# LocalWiki page type requirements (v0.12 defaults)
version: "0.12.0"
types:
  Concept:
    required_sections: ["定义", "证据", "关联"]
  Topic:
    required_sections: ["综合", "一致点", "分歧"]
  Entity:
    required_sections: ["概述"]
  Memory:
    required_sections: ["陈述", "证据"]
    kw_required: ["kw.memory"]
  Persona Trait:
    required_sections: ["维度", "证据"]
    kw_required: ["kw.persona"]
"""

DEFAULT_LINT_RULES_YAML = """# LocalWiki lint rules (v0.12 defaults)
version: "0.12.0"
rules:
  broken_links:
    enabled: true
    severity: error
  orphan_pages:
    enabled: true
    severity: warning
  missing_evidence:
    enabled: true
    severity: warning
  contradictions:
    enabled: true
    severity: error
  stale_content:
    enabled: true
    severity: info
  duplicate_concepts:
    enabled: true
    severity: warning
  missing_concepts:
    enabled: true
    severity: info
  schema_violations:
    enabled: true
    severity: error
  unprotected_sensitive:
    enabled: true
    severity: error
"""

DEFAULT_INGEST_PLAYBOOK_MD = """# Ingest playbook

1. 识别资料格式与归属（本人 / 他人 / AI）。
2. 提取文本与定位表。
3. 对照 schema 与 index.md 生成知识提案。
4. 并行产出记忆 / 经验 / 风格候选（进入待确认，不直接提交）。
5. 记录编译签名（含 schema 版本）。
"""

DEFAULT_MODEL_ROUTES = {
    "version": 2,
    "providers": {
        "ollama_local": {
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "location": "local",
        }
    },
    "routes": {
        "chat": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "agent": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "wiki_compile": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "wiki_query": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "wiki_lint": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "memory_extract": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "memory_consolidate": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "experience": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "persona": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "embedding": {
            "primary": {"provider": "ollama_local", "model": "bge-m3"},
            "fallback": [],
        },
        "vision": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "reflection": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
        "nightly": {
            "primary": {"provider": "ollama_local", "model": ""},
            "fallback": [],
        },
    },
    "budgets": {
        "agent": {"max_tokens_per_task": 400000},
        "nightly": {"max_tokens_per_day": 300000},
        "evolution": {"max_tokens_per_day": 200000},
    },
}

DEFAULT_SKILLS_REGISTRY = {
    "version": 1,
    "skills": [],
}

DEFAULT_EVOLUTION_PARAMS = {
    "version": 1,
    "autonomy_level": "E1",
    "daily_budget": {},
}

DEFAULT_LEARNINGS_STUB = """# Learnings

<!-- LocalWiki v0.12 learnings log (compatible with .learnings format). -->
"""

DEFAULT_EVOLUTION_LOG = """# Evolution log

<!-- Append-only evolution events. -->
"""


@dataclass
class MigrateAction:
    kind: str  # mkdir | write_file | skip_exists
    path: str
    detail: str = ""


@dataclass
class MigrateReport:
    workspace: str
    dry_run: bool
    schema_version: str = SCHEMA_VERSION
    created_dirs: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    actions: list[MigrateAction] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _rel(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_existing_files(workspace: Path) -> dict[str, str]:
    """Hash all existing files under workspace (files only)."""
    out: dict[str, str] = {}
    if not workspace.exists():
        return out
    for p in sorted(workspace.rglob("*")):
        if p.is_file():
            out[_rel(workspace, p)] = file_sha256(p)
    return out


def _ensure_dir(
    workspace: Path, rel: str, dry_run: bool, report: MigrateReport
) -> None:
    path = workspace / rel
    if path.exists():
        if path.is_dir():
            report.actions.append(
                MigrateAction("skip_exists", rel, "directory already present")
            )
            report.skipped_existing.append(rel)
            return
        raise NotADirectoryError(f"Expected directory but found file: {path}")
    report.actions.append(MigrateAction("mkdir", rel))
    report.created_dirs.append(rel)
    if not dry_run:
        path.mkdir(parents=True, exist_ok=True)


def _ensure_file(
    workspace: Path,
    rel: str,
    content: str | bytes,
    dry_run: bool,
    report: MigrateReport,
) -> None:
    path = workspace / rel
    if path.exists():
        report.actions.append(
            MigrateAction("skip_exists", rel, "file already present; not overwritten")
        )
        report.skipped_existing.append(rel)
        return
    # Parent may need creation as part of migration.
    parent_rel = str(Path(rel).parent).replace("\\", "/")
    if parent_rel not in (".", ""):
        _ensure_dir(workspace, parent_rel, dry_run, report)
    report.actions.append(MigrateAction("write_file", rel))
    report.created_files.append(rel)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")


def planned_directories() -> list[str]:
    return [
        "schema",
        "raw",
        "raw/chats",
        "raw/agent-sessions",
        "raw/behavior",
        "wiki",
        "wiki/memories",
        "wiki/memories/semantic",
        "wiki/memories/procedural",
        "wiki/memories/preference",
        "wiki/memories/relational",
        "wiki/profiles",
        "wiki/profiles/style",
        "wiki/system",
        "wiki/system/skills",
        "wiki/system/connections",
        "outputs",
        ".kw",
        ".kw/policy",
        ".kw/cache",
        ".kw/runtime",
        ".kw/runtime/agents",
        ".kw/runtime/chats",
        ".kw/runtime/projects",
        ".kw/runtime/browser_profiles",
        ".kw/runtime/tasks",
        ".kw/runtime/mcp",
        ".kw/memory",
        ".kw/memory/episodes",
        ".kw/skills",
        ".kw/learnings",
        ".kw/evolution",
        ".kw/evolution/prompts",
        ".kw/evolution/versions",
        ".kw/changesets",
        ".kw/history",
        ".kw/transactions",
    ]


def planned_files() -> list[tuple[str, str | bytes]]:
    return [
        ("schema/AGENTS.md", DEFAULT_AGENTS_MD),
        ("schema/TOOLS.md", DEFAULT_TOOLS_MD),
        ("schema/page-types.yaml", DEFAULT_PAGE_TYPES_YAML),
        ("schema/lint-rules.yaml", DEFAULT_LINT_RULES_YAML),
        ("schema/ingest-playbook.md", DEFAULT_INGEST_PLAYBOOK_MD),
        (
            ".kw/model-routes.json",
            json.dumps(DEFAULT_MODEL_ROUTES, ensure_ascii=False, indent=2) + "\n",
        ),
        (
            ".kw/skills/registry.json",
            json.dumps(DEFAULT_SKILLS_REGISTRY, ensure_ascii=False, indent=2) + "\n",
        ),
        (
            ".kw/evolution/params.json",
            json.dumps(DEFAULT_EVOLUTION_PARAMS, ensure_ascii=False, indent=2) + "\n",
        ),
        (".kw/evolution/log.md", DEFAULT_EVOLUTION_LOG),
        (".kw/learnings/LEARNINGS.md", DEFAULT_LEARNINGS_STUB),
        (".kw/learnings/ERRORS.md", DEFAULT_LEARNINGS_STUB.replace("Learnings", "Errors")),
        (
            ".kw/learnings/FEATURE_REQUESTS.md",
            DEFAULT_LEARNINGS_STUB.replace("Learnings", "Feature requests"),
        ),
        (".kw/learnings/HEALS.md", DEFAULT_LEARNINGS_STUB.replace("Learnings", "Heals")),
        # Empty vectors DB placeholder is a zero-byte file; real schema created later.
        (".kw/cache/vectors.sqlite", b""),
    ]


def migrate(workspace: Path, *, dry_run: bool = False) -> MigrateReport:
    workspace = workspace.resolve()
    report = MigrateReport(workspace=str(workspace), dry_run=dry_run)

    if not dry_run:
        workspace.mkdir(parents=True, exist_ok=True)

    for rel in planned_directories():
        _ensure_dir(workspace, rel, dry_run, report)

    for rel, content in planned_files():
        _ensure_file(workspace, rel, content, dry_run, report)

    # Deduplicate if ensure_file also mkdir'd parents already listed.
    report.created_dirs = list(dict.fromkeys(report.created_dirs))
    report.created_files = list(dict.fromkeys(report.created_files))
    report.skipped_existing = list(dict.fromkeys(report.skipped_existing))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate LocalWiki workspace 0.11 → 0.12 (add-only)."
    )
    parser.add_argument(
        "workspace",
        type=Path,
        help="Path to LocalWikiData (or empty/new workspace directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan actions without creating or writing anything",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print report as JSON",
    )
    args = parser.parse_args(argv)

    before = hash_existing_files(args.workspace) if args.workspace.exists() else {}
    report = migrate(args.workspace, dry_run=args.dry_run)
    after = (
        hash_existing_files(args.workspace)
        if args.workspace.exists() and not args.dry_run
        else before
    )

    # Verify pre-existing files unchanged.
    changed = []
    for rel, digest in before.items():
        if after.get(rel) != digest:
            changed.append(rel)

    payload = report.to_dict()
    payload["preexisting_hashes_unchanged"] = len(changed) == 0
    payload["changed_preexisting"] = changed

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "DRY-RUN" if args.dry_run else "APPLY"
        print(f"[{mode}] workspace={report.workspace}")
        print(f"  created_dirs:  {len(report.created_dirs)}")
        print(f"  created_files: {len(report.created_files)}")
        print(f"  skipped:       {len(report.skipped_existing)}")
        print(f"  preexisting unchanged: {len(changed) == 0}")
        if changed:
            print("  CHANGED FILES:")
            for c in changed:
                print(f"    - {c}")

    if changed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
