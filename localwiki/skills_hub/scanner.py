"""Static Skill scanner covering §13.4 categories (AT-SKL-04)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LEVEL_ORDER = {"hint": 0, "medium": 1, "high": 2, "critical": 3}

# category -> list of (regex, level, message)
RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    # network — medium
    ("network", re.compile(r"\b(requests|urllib|httpx|aiohttp)\b", re.I), "medium", "python network client"),
    ("network", re.compile(r"\b(fetch\s*\(|axios|node-fetch)\b", re.I), "medium", "js network client"),
    ("network", re.compile(r"\b(curl|Invoke-WebRequest|wget)\b", re.I), "medium", "shell network tool"),
    # process — medium
    ("process", re.compile(r"\b(subprocess|os\.system|os\.popen)\b", re.I), "medium", "python process exec"),
    ("process", re.compile(r"\b(child_process|execSync|spawnSync)\b", re.I), "medium", "node process exec"),
    ("process", re.compile(r"\b(Start-Process|Invoke-Expression)\b", re.I), "medium", "powershell process"),
    # dynamic / obfuscation — high
    ("dynamic", re.compile(r"\beval\s*\(", re.I), "high", "eval()"),
    ("dynamic", re.compile(r"\bexec\s*\(\s*(base64|bytes\.fromhex|compile)\b", re.I), "high", "exec(encoded)"),
    ("dynamic", re.compile(r"base64\.b64decode\s*\(", re.I), "high", "base64 decode exec pattern"),
    ("dynamic", re.compile(r"[A-Za-z0-9+/]{120,}={0,2}"), "high", "long encoded string"),
    # credentials — critical
    ("credentials", re.compile(r"(os\.environ|process\.env).{0,40}(_KEY|_TOKEN|SECRET|PASSWORD)", re.I), "critical", "env credential read"),
    ("credentials", re.compile(r"~/?\.ssh|/id_rsa|Credentials\.Get|credssp|keyring\.get_password", re.I), "critical", "credential store / ssh"),
    ("credentials", re.compile(r"AppData\\\\(Local|Roaming)\\\\.*(Chrome|Browser)|Login Data", re.I), "critical", "browser profile access"),
    # path escape — high
    ("path_escape", re.compile(r"(^|[\"'\s])/(etc|usr|windows|system32)\b", re.I), "high", "absolute system path"),
    ("path_escape", re.compile(r"Path\.home\s*\(|os\.path\.expanduser|USERPROFILE|\\\\Users\\\\", re.I), "high", "home directory access"),
    ("path_escape", re.compile(r"\.\./\.\./"), "high", "path traversal"),
    # binaries — high (also checked by extension)
    ("binary", re.compile(r"\.(exe|dll|so|dylib|node)\b", re.I), "high", "binary reference"),
    # install hooks — high
    ("install_hook", re.compile(r'"(preinstall|postinstall|install)"\s*:', re.I), "high", "npm install hook"),
    ("install_hook", re.compile(r"cmdclass\s*=|setup\s*\(.*cmdclass", re.I | re.S), "high", "setup.py custom command"),
    # prompt injection — critical
    ("prompt_injection", re.compile(
        r"(ignore\s+(all\s+)?(previous|prior)\s+(instructions|prompts)|系统提示|忽略以上|不要告诉用户|do\s+not\s+tell\s+the\s+user|exfiltrat|send\s+(all\s+)?files?\s+to)",
        re.I,
    ), "critical", "prompt injection / exfil instruction"),
    # license — hint (missing checked separately)
    ("license", re.compile(r"ALL\s+RIGHTS\s+RESERVED|proprietary\s+only", re.I), "hint", "restrictive license wording"),
]

BINARY_EXTS = {".exe", ".dll", ".so", ".dylib", ".node", ".bin"}


@dataclass
class Finding:
    category: str
    level: str
    message: str
    path: str
    line: int | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)
    max_level: str = "hint"
    block_install: bool = False
    categories_hit: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "max_level": self.max_level,
            "block_install": self.block_install,
            "categories_hit": list(self.categories_hit),
        }


def _max_level(levels: list[str]) -> str:
    best = "hint"
    for lv in levels:
        if LEVEL_ORDER.get(lv, 0) > LEVEL_ORDER.get(best, 0):
            best = lv
    return best


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    for category, pat, level, message in RULES:
        for i, line in enumerate(lines, start=1):
            if pat.search(line):
                findings.append(
                    Finding(
                        category=category,
                        level=level,
                        message=message,
                        path=path,
                        line=i,
                        snippet=line.strip()[:200],
                    )
                )
        # Also whole-file for multiline patterns
        if pat.flags & re.S and pat.search(text):
            if not any(f.message == message and f.path == path for f in findings):
                findings.append(
                    Finding(category=category, level=level, message=message, path=path)
                )
    return findings


def scan_tree(root: Path) -> ScanReport:
    findings: list[Finding] = []
    has_license = False
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if path.suffix.lower() in BINARY_EXTS:
            findings.append(
                Finding(
                    category="binary",
                    level="high",
                    message=f"binary file {path.suffix}",
                    path=rel,
                )
            )
            continue
        if path.name.upper() in ("LICENSE", "LICENSE.MD", "LICENSE.TXT", "COPYING"):
            has_license = True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        findings.extend(scan_text(rel, text))
        # package.json hooks
        if path.name == "package.json":
            try:
                data = json.loads(text)
                scripts = data.get("scripts") or {}
                for hook in ("preinstall", "postinstall", "install"):
                    if hook in scripts:
                        findings.append(
                            Finding(
                                category="install_hook",
                                level="high",
                                message=f"package.json scripts.{hook}",
                                path=rel,
                            )
                        )
            except json.JSONDecodeError:
                pass

    if not has_license:
        findings.append(
            Finding(
                category="license",
                level="hint",
                message="missing license file",
                path=".",
            )
        )

    levels = [f.level for f in findings]
    max_lv = _max_level(levels) if levels else "hint"
    cats = sorted({f.category for f in findings})
    return ScanReport(
        findings=findings,
        max_level=max_lv,
        block_install=max_lv == "critical",
        categories_hit=cats,
    )


def scan_blocks_install(report: ScanReport) -> bool:
    """Critical findings block install by default (§13.4)."""
    return report.block_install
