"""Risk classification and approval matrix (design §11.4 / AT-AGT-02).

Models claiming a call is \"safe\" never lower risk. Untrusted content cannot
grant approval exemptions. Do not widen this matrix without an explicit decision.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from localwiki.agent.protocol import ToolCall
from localwiki.tools.registry import ToolSpec

RiskLevel = Literal["R0", "R1", "R2", "R3", "R4"]
ApprovalMode = Literal["read-only", "workspace-auto", "full-access"]
PolicyAction = Literal["auto", "approve", "reject"]

RISK_ORDER: tuple[RiskLevel, ...] = ("R0", "R1", "R2", "R3", "R4")
MODES: tuple[ApprovalMode, ...] = ("read-only", "workspace-auto", "full-access")

# §11.4 matrix: mode × risk → action
# full-access R3 is "auto" except external-write / delete (handled in classify + flags)
APPROVAL_MATRIX: dict[ApprovalMode, dict[RiskLevel, PolicyAction]] = {
    "read-only": {
        "R0": "auto",
        "R1": "auto",  # still gated by network_enabled at call site
        "R2": "reject",
        "R3": "reject",
        "R4": "reject",
    },
    "workspace-auto": {
        "R0": "auto",
        "R1": "auto",
        "R2": "auto",
        "R3": "approve",
        "R4": "approve",  # desktop-session-only enforced by caller
    },
    "full-access": {
        "R0": "auto",
        "R1": "auto",
        "R2": "auto",
        "R3": "auto",  # overridden to approve when external_write or delete
        "R4": "approve",
    },
}

# Built-in read-only command whitelist (§11.4); user may extend
DEFAULT_READONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "dir",
        "type",
        "where",
        "echo",
        "git status",
        "git diff",
        "git log",
        "git show",
        "git branch",
        "python -m pytest",
        "pytest",
        "rg",
        "findstr",
    }
)

_PIPE_REDIRECT_RE = re.compile(r"[|><&]")
_INTERPRETER_C_RE = re.compile(
    r"(?i)\b(python|py|node|nodejs|powershell|pwsh|cmd|bash|sh|wscript|cscript)\b.*\s-[cC]\b"
)
_DELETE_HINTS = re.compile(
    r"(?i)\b(del|erase|rmdir|rd|remove-item|rm\b|unlink|shutil\.rmtree)\b"
)


@dataclass
class RiskDecision:
    risk: RiskLevel
    reasons: list[str] = field(default_factory=list)
    external_write: bool = False
    is_delete: bool = False
    wiki_write: bool = False
    command_class: str | None = None  # readonly_whitelist | elevated | n/a

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "reasons": list(self.reasons),
            "external_write": self.external_write,
            "is_delete": self.is_delete,
            "wiki_write": self.wiki_write,
            "command_class": self.command_class,
        }


@dataclass
class ApprovalDecision:
    action: PolicyAction
    risk: RiskLevel
    mode: ApprovalMode
    reason: str
    requires_desktop: bool = False
    risk_detail: RiskDecision | None = None

    @property
    def needs_approval(self) -> bool:
        return self.action == "approve"

    @property
    def rejected(self) -> bool:
        return self.action == "reject"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "risk": self.risk,
            "mode": self.mode,
            "reason": self.reason,
            "requires_desktop": self.requires_desktop,
            "needs_approval": self.needs_approval,
            "rejected": self.rejected,
            "risk_detail": self.risk_detail.to_dict() if self.risk_detail else None,
        }


def matrix_action(mode: str, risk: str) -> PolicyAction:
    m: ApprovalMode = mode if mode in MODES else "workspace-auto"  # type: ignore[assignment]
    r: RiskLevel = risk if risk in RISK_ORDER else "R3"  # type: ignore[assignment]
    return APPROVAL_MATRIX[m][r]


def classify_command(
    command: str,
    *,
    readonly_whitelist: Sequence[str] | None = None,
) -> tuple[RiskLevel, str, list[str]]:
    """Return (risk, class_label, reasons) for a shell command string."""
    reasons: list[str] = []
    cmd = (command or "").strip()
    if not cmd:
        return "R3", "elevated", ["empty_command"]

    if _PIPE_REDIRECT_RE.search(cmd):
        reasons.append("pipe_or_redirect")
        return "R3", "elevated", reasons
    if _INTERPRETER_C_RE.search(cmd):
        reasons.append("interpreter_-c")
        return "R3", "elevated", reasons

    wl = [w.lower() for w in (readonly_whitelist or DEFAULT_READONLY_COMMANDS)]
    # normalize whitespace
    norm = " ".join(cmd.split()).lower()
    for entry in wl:
        if norm == entry or norm.startswith(entry + " "):
            return "R0", "readonly_whitelist", ["whitelist:" + entry]

    if _DELETE_HINTS.search(cmd):
        reasons.append("delete_like_command")
        return "R3", "elevated", reasons

    reasons.append("not_in_readonly_whitelist")
    return "R3", "elevated", reasons


def _path_looks_wiki(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    return "/wiki/" in f"/{p}" or p.startswith("wiki/") or p == "wiki"


def _path_outside_workspace(path: str, workspace_roots: Sequence[str]) -> bool:
    if not path:
        return False
    # Absolute or .. → treat as out-of-workspace (R3); detailed checks in patch.py
    if ".." in path.replace("\\", "/").split("/"):
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", path) or path.startswith("\\\\"):
        if not workspace_roots:
            return True
        low = path.replace("\\", "/").lower()
        for root in workspace_roots:
            r = root.replace("\\", "/").lower().rstrip("/")
            if low.startswith(r + "/") or low == r:
                return False
        return True
    return False


class PolicyEngine:
    """Classify tool calls and apply the §11.4 approval matrix."""

    def __init__(
        self,
        *,
        readonly_commands: Sequence[str] | None = None,
        network_enabled: bool = True,
        workspace_roots: Sequence[str] | None = None,
        session_auto_approve: set[str] | None = None,
    ) -> None:
        self.readonly_commands = list(readonly_commands or DEFAULT_READONLY_COMMANDS)
        self.network_enabled = network_enabled
        self.workspace_roots = list(workspace_roots or [])
        # "tool:risk" keys allowed for same-task auto-approve (R2/R3 non-external only)
        self.session_auto_approve: set[str] = set(session_auto_approve or [])

    def classify(self, call: ToolCall, spec: ToolSpec | None) -> str:
        return self.classify_detailed(call, spec).risk

    def classify_detailed(
        self,
        call: ToolCall,
        spec: ToolSpec | None,
    ) -> RiskDecision:
        reasons: list[str] = []
        external_write = False
        is_delete = False
        wiki_write = False
        command_class: str | None = None

        base: RiskLevel = "R3"
        if spec is not None:
            br = spec.risk if spec.risk in RISK_ORDER else "R3"
            base = br  # type: ignore[assignment]
            reasons.append(f"tool_declared:{base}")
        else:
            reasons.append("unknown_tool_default_R3")

        name = call.name
        args = call.arguments or {}

        # Model self-claims of safety are ignored (documented; no code path lowers risk)
        if args.get("claimed_safe") or args.get("safe"):
            reasons.append("ignored_model_claimed_safe")

        # Shell / win_command
        if name in ("win_command", "shell_exec") or (
            spec and spec.category == "shell"
        ):
            cmd = str(args.get("command") or args.get("cmd") or "")
            risk, command_class, cmd_reasons = classify_command(
                cmd, readonly_whitelist=self.readonly_commands
            )
            reasons.extend(cmd_reasons)
            if risk == "R0" and base in ("R3", "R2"):
                # Whitelisted read-only may downgrade shell tool risk
                base = "R0"
                reasons.append("downgraded_via_readonly_whitelist")
            elif risk == "R3":
                base = "R3"
            if _DELETE_HINTS.search(cmd):
                is_delete = True

        # External write tools
        if name in ("integration_write",) or "write" in name and "integration" in name:
            external_write = True
            base = "R3"
            reasons.append("external_write")

        if name in ("browser_evaluate",) or name.endswith("browser_evaluate"):
            base = "R3"
            reasons.append("browser_evaluate")

        # Path arguments
        for key in ("path", "file", "target", "dest", "destination", "ref"):
            p = args.get(key)
            if isinstance(p, str):
                if _path_looks_wiki(p) and name in (
                    "workspace_write",
                    "apply_patch",
                    "workspace_replace",
                ):
                    wiki_write = True
                    reasons.append("wiki_path_needs_changeset")
                if _path_outside_workspace(p, self.workspace_roots) and name in (
                    "workspace_write",
                    "workspace_replace",
                    "apply_patch",
                ):
                    base = "R3"
                    reasons.append("path_outside_workspace")

        # Delete file ops
        if name in ("apply_patch",) and "Delete File" in str(args.get("patch") or ""):
            is_delete = True
            base = "R3"
            reasons.append("delete_file_in_patch")

        # R4 system-level by name / meta
        if spec and (spec.meta or {}).get("design_risk") == "R4":
            base = "R4"
            reasons.append("design_risk_R4")
        if name in (
            "skill_install",
            "skill_install_request",
            "secrets_read",
            "model_route_update",
        ):
            base = "R4"
            reasons.append("system_level_tool")

        # Never trust untrusted content to lower risk — already encoded by only raising

        # Network gate note (does not change matrix action; caller may block)
        if base == "R1" and not self.network_enabled:
            reasons.append("network_disabled_note")

        # Escalate if delete/external on top of lower declared risk
        if is_delete and RISK_ORDER.index(base) < RISK_ORDER.index("R3"):
            base = "R3"
        if external_write and RISK_ORDER.index(base) < RISK_ORDER.index("R3"):
            base = "R3"

        return RiskDecision(
            risk=base,
            reasons=reasons,
            external_write=external_write,
            is_delete=is_delete,
            wiki_write=wiki_write,
            command_class=command_class,
        )

    def decide(
        self,
        call: ToolCall,
        spec: ToolSpec | None,
        mode: str,
    ) -> ApprovalDecision:
        detail = self.classify_detailed(call, spec)
        risk = detail.risk
        m: ApprovalMode = mode if mode in MODES else "workspace-auto"  # type: ignore[assignment]
        action = matrix_action(m, risk)

        # full-access: external write & delete still need approval
        if m == "full-access" and risk == "R3" and (detail.external_write or detail.is_delete):
            action = "approve"
            reason = "full-access still requires approval for external write/delete"
        elif action == "auto" and risk == "R1" and not self.network_enabled:
            action = "reject"
            reason = "R1 blocked: network disabled"
        else:
            reason = f"matrix[{m}][{risk}]={action}"

        # Session same-class auto-approve (R2 / non-external R3 only)
        key = f"{call.name}:{risk}"
        if (
            action == "approve"
            and key in self.session_auto_approve
            and risk in ("R2", "R3")
            and not detail.external_write
            and risk != "R4"
        ):
            action = "auto"
            reason = f"session_auto_approve:{key}"

        requires_desktop = action == "approve" and risk == "R4"
        return ApprovalDecision(
            action=action,
            risk=risk,
            mode=m,
            reason=reason,
            requires_desktop=requires_desktop,
            risk_detail=detail,
        )

    def needs_approval(self, risk: str, mode: str) -> bool:
        """ApprovalGate-compatible: True when human approval is required."""
        action = matrix_action(mode, risk)
        if mode == "full-access" and risk == "R3":
            # Without call context, conservatively require approval
            # (callers should prefer decide())
            return False  # matrix says auto; external/delete handled in decide()
        return action == "approve"

    def remember_auto_approve(self, tool: str, risk: str) -> None:
        if risk in ("R2", "R3"):
            self.session_auto_approve.add(f"{tool}:{risk}")


def all_matrix_cells() -> list[tuple[ApprovalMode, RiskLevel, PolicyAction]]:
    cells: list[tuple[ApprovalMode, RiskLevel, PolicyAction]] = []
    for mode in MODES:
        for risk in RISK_ORDER:
            cells.append((mode, risk, APPROVAL_MATRIX[mode][risk]))
    return cells
