"""Improvement proposals + freeze-zone rejection (design §23.2 / §23.8 / LW-43).

Any proposal that touches a freeze zone is rejected at generation time and
logged to ``.kw/evolution/evolution.log``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from localwiki.evolution.aggregate import (
    PatternAggregator,
    PromotionCandidate,
    short_preventive_rule,
)

ProposalStatus = Literal[
    "proposed",
    "evaluating",
    "passed",
    "failed",
    "awaiting_approval",
    "applied",
    "monitoring",
    "stable",
    "rolled_back",
    "rejected",
]

RiskTier = Literal["V0", "V1", "V2"]

# §23.2 freeze zones — path / keyword patterns (case-insensitive)
FREEZE_PATH_GLOBS = (
    # Own source — never mutate LocalWiki code via evolution
    r"(^|/)localwiki/",
    r"(^|/)web/.*\.(js|mjs|css|html)$",
    r"(^|/)scripts/",
    r"(^|/)tests/",
    # Secrets / backup / deletion
    r"secrets?\.py$",
    r"(^|/)\.kw/secrets",
    r"backup",
    r"purge",
    r"deletion",
    # Eval suite (frozen) + judge rules
    r"(^|/)evals/",
    r"eval[_-]?metrics",
    r"judge[_-]?rules",
    # Evolution gate params themselves
    r"evolution/(apply|gate|budget)",
    r"gate_params",
    # Approval / policy / privacy / sandbox sources
    r"agent/policy\.py$",
    r"agent/sandbox\.py$",
    r"privacy",
    r"model_routing\.py$",
)

FREEZE_CONTENT_KEYWORDS = (
    # Approval matrix §11.4
    "approval_matrix",
    "审批模式",
    "审批矩阵",
    "risk_tier_matrix",
    "full-access",
    "auto-approve",
    "auto_approve",
    # Privacy §17.3 / §14.4
    "privacy_gate",
    "出境矩阵",
    "隐私门控",
    "privacy_class",
    "egress_matrix",
    "classification_matrix",
    # Secrets / backup
    "keyring",
    "dpapi",
    "secret_store",
    "backup_policy",
    "delete_policy",
    "deletion_policy",
    # Eval freeze
    "golden_suite",
    "基准集",
    "evals/",
    "judge_rubric",
    "评判规则",
    # Own source / gate self-modification
    "localwiki/",
    "freeze_zone",
    "门禁参数",
    "gate_params",
    "evolution_budget_bypass",
)

# Allowed evolution targets (§23.2 table) → default risk
ALLOWED_TARGET_RISK: dict[str, RiskTier] = {
    "params": "V0",
    "tool_note": "V1",
    "prompt_variant": "V1",
    "agents_rule": "V2",
    "schema_revision": "V2",
    "routing_suggestion": "V2",
    "skill_extract_suggestion": "V2",
    "retrieval_params": "V0",
    "dev_backlog": "V0",  # record only
    "user_candidate_handoff": "V0",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_uid() -> str:
    return f"imp_{uuid.uuid4().hex[:16]}"


@dataclass
class FreezeHit:
    rule: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "detail": self.detail}


@dataclass
class ImprovementProposal:
    proposal_uid: str
    kind: str
    target: dict[str, str]
    diff: str
    rationale: str
    evidence: list[str] = field(default_factory=list)
    risk_tier: RiskTier | str = "V1"
    expected_effect: dict[str, str] = field(default_factory=dict)
    eval_plan: dict[str, Any] = field(default_factory=dict)
    eval_result: dict[str, Any] | None = None
    status: ProposalStatus | str = "proposed"
    evolution_version_base: str = "evo_000000"
    rollback: dict[str, str] = field(default_factory=dict)
    pattern_key: str = ""
    reject_reason: str = ""
    freeze_hits: list[FreezeHit] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["freeze_hits"] = [h.to_dict() if isinstance(h, FreezeHit) else h for h in self.freeze_hits]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def check_freeze_zone(
    *,
    target: dict[str, str] | None = None,
    diff: str = "",
    kind: str = "",
    rationale: str = "",
    extra_paths: Sequence[str] | None = None,
) -> list[FreezeHit]:
    """Return freeze-zone hits; empty list means allowed (§23.2 / AT-EVO-05)."""
    hits: list[FreezeHit] = []
    target = target or {}
    paths = [str(target.get("file") or ""), str(target.get("path") or "")]
    if extra_paths:
        paths.extend(str(p) for p in extra_paths)
    blob = " ".join(
        [
            kind,
            rationale,
            diff,
            json.dumps(target, ensure_ascii=False),
            " ".join(paths),
        ]
    ).lower()

    for path in paths:
        if not path:
            continue
        norm = path.replace("\\", "/")
        for pat in FREEZE_PATH_GLOBS:
            if re.search(pat, norm, re.I):
                hits.append(FreezeHit("freeze_path", f"{norm} matches /{pat}/"))

    for kw in FREEZE_CONTENT_KEYWORDS:
        if kw.lower() in blob:
            hits.append(FreezeHit("freeze_content", f"keyword:{kw}"))

    # Explicit denial: mutating approval / privacy matrices by kind
    if kind in ("approval_matrix_change", "privacy_gate_change", "secret_policy_change", "source_edit"):
        hits.append(FreezeHit("freeze_kind", f"kind={kind}"))

    # Dedup by detail
    seen: set[str] = set()
    uniq: list[FreezeHit] = []
    for h in hits:
        key = f"{h.rule}:{h.detail}"
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    return uniq


class EvolutionLog:
    """Append-only evolution decision log under ``.kw/evolution/``."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.dir = self.root / ".kw" / "evolution"
        self.path = self.dir / "evolution.log"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, payload: dict[str, Any]) -> None:
        line = json.dumps(
            {"ts": _now_iso(), "event": event, **payload},
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class ProposalStore:
    """Persist proposals as JSON files; rejected ones still logged."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.dir = self.root / ".kw" / "evolution" / "proposals"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = EvolutionLog(self.root)

    def path_for(self, uid: str) -> Path:
        return self.dir / f"{uid}.json"

    def save(self, proposal: ImprovementProposal) -> Path:
        if not proposal.created_at:
            proposal.created_at = _now_iso()
        path = self.path_for(proposal.proposal_uid)
        path.write_text(proposal.to_json() + "\n", encoding="utf-8")
        self.log.write(
            "proposal_saved",
            {
                "proposal_uid": proposal.proposal_uid,
                "status": proposal.status,
                "kind": proposal.kind,
                "reject_reason": proposal.reject_reason,
            },
        )
        return path

    def list_proposals(self, status: str | None = None) -> list[ImprovementProposal]:
        out: list[ImprovementProposal] = []
        for p in sorted(self.dir.glob("imp_*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            hits = [FreezeHit(**h) if isinstance(h, dict) else h for h in data.pop("freeze_hits", [])]
            prop = ImprovementProposal(**{k: v for k, v in data.items() if k in ImprovementProposal.__dataclass_fields__})
            prop.freeze_hits = hits
            if status is None or prop.status == status:
                out.append(prop)
        return out


def risk_for_kind(kind: str) -> RiskTier:
    return ALLOWED_TARGET_RISK.get(kind, "V2")


def proposal_from_candidate(
    cand: PromotionCandidate,
    *,
    evolution_version_base: str = "evo_000000",
) -> ImprovementProposal:
    kind = cand.target.get("kind") or "tool_note"
    diff = cand.preventive_rule
    if not diff.startswith("+") and not diff.startswith("-"):
        diff = f"+ {diff.lstrip('- ').strip()}"
    elif diff.startswith("- "):
        # markdown bullet → proposal diff add
        diff = f"+ {diff}"
    return ImprovementProposal(
        proposal_uid=cand.proposal_uid or _new_uid(),
        kind=kind,
        target=dict(cand.target),
        diff=diff,
        rationale=(
            f"Pattern-Key={cand.pattern_key}; reason={cand.reason}; "
            f"rec={cand.countable_recurrence}; tasks={cand.distinct_tasks}"
        ),
        evidence=list(cand.record_ids),
        risk_tier=risk_for_kind(kind),
        expected_effect={"metric": f"pattern:{cand.pattern_key}", "direction": "down"},
        eval_plan={
            "replay_tasks": min(3, max(1, cand.countable_recurrence)),
            "golden_suites": [],
            "max_regression": 0.01,
        },
        eval_result=None,
        status="proposed",
        evolution_version_base=evolution_version_base,
        rollback={"type": "revert_to", "version": evolution_version_base},
        pattern_key=cand.pattern_key,
        created_at=_now_iso(),
    )


class ProposalGenerator:
    """Build proposals from promotion candidates; reject freeze-zone touches."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.store = ProposalStore(self.root)
        self.log = self.store.log

    def generate(
        self,
        *,
        kind: str,
        target: dict[str, str],
        diff: str,
        rationale: str,
        evidence: Sequence[str] | None = None,
        pattern_key: str = "",
        evolution_version_base: str = "evo_000000",
        expected_effect: dict[str, str] | None = None,
    ) -> ImprovementProposal:
        """Create one proposal or a rejected stub if freeze zone is hit."""
        hits = check_freeze_zone(target=target, diff=diff, kind=kind, rationale=rationale)
        uid = _new_uid()
        if hits:
            prop = ImprovementProposal(
                proposal_uid=uid,
                kind=kind,
                target=dict(target),
                diff=diff,
                rationale=rationale,
                evidence=list(evidence or []),
                risk_tier=risk_for_kind(kind),
                status="rejected",
                reject_reason="freeze_zone",
                freeze_hits=hits,
                pattern_key=pattern_key,
                evolution_version_base=evolution_version_base,
                rollback={"type": "none", "version": evolution_version_base},
                created_at=_now_iso(),
            )
            self.store.save(prop)
            self.log.write(
                "proposal_rejected_freeze",
                {
                    "proposal_uid": uid,
                    "hits": [h.to_dict() for h in hits],
                    "target": target,
                },
            )
            return prop

        prop = ImprovementProposal(
            proposal_uid=uid,
            kind=kind,
            target=dict(target),
            diff=diff if diff.startswith("+") or diff.startswith("-") else f"+ {diff}",
            rationale=rationale,
            evidence=list(evidence or []),
            risk_tier=risk_for_kind(kind),
            expected_effect=expected_effect
            or {"metric": pattern_key or "unspecified", "direction": "improve"},
            eval_plan={"replay_tasks": 3, "golden_suites": [], "max_regression": 0.01},
            status="proposed",
            evolution_version_base=evolution_version_base,
            rollback={"type": "revert_to", "version": evolution_version_base},
            pattern_key=pattern_key,
            created_at=_now_iso(),
        )
        self.store.save(prop)
        return prop

    def from_promotion(
        self,
        cand: PromotionCandidate,
        *,
        evolution_version_base: str = "evo_000000",
    ) -> ImprovementProposal:
        draft = proposal_from_candidate(cand, evolution_version_base=evolution_version_base)
        # Re-run freeze check (promotion targets should be safe, but enforce anyway)
        return self.generate(
            kind=draft.kind,
            target=draft.target,
            diff=draft.diff,
            rationale=draft.rationale,
            evidence=draft.evidence,
            pattern_key=draft.pattern_key,
            evolution_version_base=evolution_version_base,
            expected_effect=draft.expected_effect,
        )

    def from_aggregator(
        self,
        aggregator: PatternAggregator,
        *,
        evolution_version_base: str = "evo_000000",
        mark_learnings: bool = True,
    ) -> list[ImprovementProposal]:
        cands = aggregator.promote_eligible(mark=mark_learnings)
        return [self.from_promotion(c, evolution_version_base=evolution_version_base) for c in cands]


# --- Counter-example builders for AT-EVO-05 tests ---

FREEZE_COUNTEREXAMPLES: list[dict[str, Any]] = [
    {
        "name": "approval_matrix",
        "kind": "approval_matrix_change",
        "target": {"file": "localwiki/agent/policy.py", "anchor": "approval_matrix"},
        "diff": "+ auto_approve R3 in read-only mode",
        "rationale": "放宽审批矩阵以减少打断",
    },
    {
        "name": "privacy_gate",
        "kind": "privacy_gate_change",
        "target": {"file": "localwiki/model_routing.py", "anchor": "privacy_gate"},
        "diff": "+ allow private memory to egress online",
        "rationale": "绕过隐私门控 / 出境矩阵",
    },
    {
        "name": "evals_suite",
        "kind": "eval_edit",
        "target": {"file": "evals/agent/tasks.jsonl", "anchor": "suite"},
        "diff": "+ remove hard failing case",
        "rationale": "修改基准集使评估更容易通过",
    },
    {
        "name": "own_source",
        "kind": "source_edit",
        "target": {"file": "localwiki/evolution/aggregate.py", "anchor": "promote"},
        "diff": "+ lower recurrence threshold to 1",
        "rationale": "修改自进化源代码",
    },
    {
        "name": "secrets",
        "kind": "secret_policy_change",
        "target": {"file": "localwiki/secrets.py", "anchor": "store"},
        "diff": "+ store api keys in plaintext config",
        "rationale": "放宽秘密存储",
    },
]


def assert_all_freeze_counterexamples_rejected(gen: ProposalGenerator) -> list[ImprovementProposal]:
    """AT-EVO-05 helper: every freeze counterexample must be rejected."""
    out: list[ImprovementProposal] = []
    for ex in FREEZE_COUNTEREXAMPLES:
        prop = gen.generate(
            kind=ex["kind"],
            target=ex["target"],
            diff=ex["diff"],
            rationale=ex["rationale"],
            evidence=["counterexample"],
        )
        if prop.status != "rejected" or prop.reject_reason != "freeze_zone":
            raise AssertionError(f"freeze counterexample not rejected: {ex['name']}")
        out.append(prop)
    return out
