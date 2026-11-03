"""Autonomy gates, evolution versions, monitoring, rollback (§23.10–23.11 / LW-45).

E1 (default): all proposals need user confirmation.
E2: V0/V1 auto-apply after eval pass; V2 still needs confirmation.
Freeze-zone: always reject. E3 not provided.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from localwiki.evolution.proposals import (
    ImprovementProposal,
    ProposalStore,
    check_freeze_zone,
)

AutonomyLevel = Literal["E0", "E1", "E2"]
GateAction = Literal["reject", "await_user", "auto_apply"]

DEFAULT_LEVEL: AutonomyLevel = "E1"
MONITOR_TASK_LIMIT = 20
MONITOR_DAY_LIMIT = 7
OSCILLATION_MAX_EDITS = 2
OSCILLATION_WINDOW_DAYS = 30
REGRESSION_THRESHOLD = 0.05  # 5% metric drop → rollback


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().astimezone().isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def gate_action(
    level: AutonomyLevel | str,
    risk_tier: str,
    *,
    freeze_hits: Sequence[Any] | None = None,
    eval_passed: bool = True,
) -> GateAction:
    """E0/E1/E2 × V0/V1/V2 matrix (§23.10). Freeze always reject."""
    if freeze_hits:
        return "reject"
    # Also treat explicit freeze risk
    if str(risk_tier).upper() == "FREEZE":
        return "reject"

    level = str(level).upper()  # type: ignore[assignment]
    risk = str(risk_tier).upper()

    if level == "E0":
        return "reject"  # no proposals applied; logging-only

    if level == "E1":
        # All non-freeze need user confirmation
        return "await_user"

    if level == "E2":
        if risk == "V2":
            return "await_user"
        if risk in ("V0", "V1"):
            return "auto_apply" if eval_passed else "await_user"
        return "await_user"

    # E3 not provided — treat unknown as safest
    return "await_user"


@dataclass
class FileChange:
    path: str
    before_hash: str
    after_hash: str
    before_text: str = ""
    after_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
        }


@dataclass
class EvolutionVersion:
    version: str
    proposal_uid: str
    created_at: str
    changes: list[FileChange] = field(default_factory=list)
    auto_applied: bool = False
    rolled_back_from: str = ""
    status: str = "active"  # active | rolled_back | superseded
    metrics_baseline: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "proposal_uid": self.proposal_uid,
            "created_at": self.created_at,
            "changes": [c.to_dict() for c in self.changes],
            "auto_applied": self.auto_applied,
            "rolled_back_from": self.rolled_back_from,
            "status": self.status,
            "metrics_baseline": dict(self.metrics_baseline),
        }


@dataclass
class MonitorState:
    version: str
    proposal_uid: str
    started_at: str
    related_tasks_seen: int = 0
    metrics: dict[str, float] = field(default_factory=dict)
    baseline_metrics: dict[str, float] = field(default_factory=dict)
    closed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvolutionRegistry:
    """Persist evolution versions + oscillation counters under ``.kw/evolution/``."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.dir = self.root / ".kw" / "evolution"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.versions_path = self.dir / "versions.jsonl"
        self.state_path = self.dir / "apply_state.json"
        self.oscillation_path = self.dir / "oscillation.json"
        self.monitor_path = self.dir / "monitor.json"
        if not self.state_path.exists():
            self._write_state(
                {
                    "level": DEFAULT_LEVEL,
                    "seq": 0,
                    "current_version": "evo_000000",
                    "frozen_targets": {},
                }
            )

    def _write_state(self, data: dict[str, Any]) -> None:
        self.state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def set_level(self, level: AutonomyLevel) -> None:
        st = self.load_state()
        st["level"] = level
        self._write_state(st)

    def next_version_id(self) -> str:
        st = self.load_state()
        st["seq"] = int(st.get("seq") or 0) + 1
        vid = f"evo_{st['seq']:06d}"
        st["current_version"] = vid
        self._write_state(st)
        return vid

    def append_version(self, ver: EvolutionVersion) -> None:
        with self.versions_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ver.to_dict(), ensure_ascii=False) + "\n")

    def list_versions(self) -> list[dict[str, Any]]:
        if not self.versions_path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.versions_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def load_oscillation(self) -> dict[str, list[str]]:
        if not self.oscillation_path.exists():
            return {}
        return json.loads(self.oscillation_path.read_text(encoding="utf-8"))

    def record_target_edit(self, target_key: str, when: datetime | None = None) -> bool:
        """Return True if target becomes oscillation-frozen (>2 edits / 30d)."""
        when = when or _now()
        data = self.load_oscillation()
        stamps = list(data.get(target_key) or [])
        stamps.append(when.isoformat())
        # prune
        cutoff = when - timedelta(days=OSCILLATION_WINDOW_DAYS)
        kept: list[str] = []
        for s in stamps:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                kept.append(dt.isoformat())
        data[target_key] = kept
        self.oscillation_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        frozen = len(kept) > OSCILLATION_MAX_EDITS
        if frozen:
            st = self.load_state()
            ft = dict(st.get("frozen_targets") or {})
            ft[target_key] = _now_iso()
            st["frozen_targets"] = ft
            self._write_state(st)
        return frozen

    def is_target_frozen(self, target_key: str) -> bool:
        st = self.load_state()
        return target_key in (st.get("frozen_targets") or {})

    def save_monitor(self, mon: MonitorState) -> None:
        self.monitor_path.write_text(
            json.dumps(mon.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_monitor(self) -> MonitorState | None:
        if not self.monitor_path.exists():
            return None
        data = json.loads(self.monitor_path.read_text(encoding="utf-8"))
        return MonitorState(**data)


def _target_key(proposal: ImprovementProposal | dict[str, Any]) -> str:
    if isinstance(proposal, ImprovementProposal):
        t = proposal.target
    else:
        t = proposal.get("target") or {}
    return f"{t.get('file','') }#{t.get('anchor','')}"


def _apply_diff_to_file(path: Path, diff: str) -> FileChange:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    # Simple append of preventive rule lines
    add_lines = []
    for line in diff.splitlines():
        s = line.strip()
        if s.startswith("+"):
            add_lines.append(s[1:].lstrip())
        elif s.startswith("- ") and not s.startswith("---"):
            # treat markdown bullet as content to add when applying tool notes
            add_lines.append(s)
    block = "\n".join(add_lines).strip()
    if block:
        after = before.rstrip() + "\n\n## 已学习的规则\n" + block + "\n"
    else:
        after = before
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(after, encoding="utf-8")
    return FileChange(
        path=str(path),
        before_hash=_sha256_text(before),
        after_hash=_sha256_text(after),
        before_text=before,
        after_text=after,
    )


class EvolutionApplier:
    """Gate → apply → monitor → rollback."""

    def __init__(self, workspace_root: Path | str) -> None:
        self.root = Path(workspace_root)
        self.registry = EvolutionRegistry(self.root)
        self.proposals = ProposalStore(self.root)
        self._rollback_blocked: set[str] = set()  # pattern/proposal keys not to re-propose

    @property
    def level(self) -> AutonomyLevel:
        return self.registry.load_state().get("level") or DEFAULT_LEVEL  # type: ignore[return-value]

    def set_level(self, level: AutonomyLevel) -> None:
        if level not in ("E0", "E1", "E2"):
            raise ValueError("E3 not provided; use E0/E1/E2")
        self.registry.set_level(level)

    def decide(
        self,
        proposal: ImprovementProposal,
        *,
        eval_passed: bool = True,
        user_confirmed: bool = False,
    ) -> GateAction:
        hits = list(proposal.freeze_hits) or check_freeze_zone(
            target=proposal.target,
            diff=proposal.diff,
            kind=proposal.kind,
            rationale=proposal.rationale,
        )
        if hits and proposal.status != "rejected":
            proposal.status = "rejected"
            proposal.reject_reason = "freeze_zone"
            proposal.freeze_hits = list(hits)

        tkey = _target_key(proposal)
        if self.registry.is_target_frozen(tkey):
            return "reject"

        action = gate_action(
            self.level,
            str(proposal.risk_tier),
            freeze_hits=hits,
            eval_passed=eval_passed,
        )
        if action == "auto_apply":
            return action
        if action == "await_user" and user_confirmed and eval_passed and not hits:
            return "auto_apply"  # confirmed → proceed to apply path
        return action

    def apply(
        self,
        proposal: ImprovementProposal,
        *,
        eval_passed: bool = True,
        user_confirmed: bool = False,
        metrics_baseline: dict[str, float] | None = None,
    ) -> EvolutionVersion | None:
        action = self.decide(
            proposal,
            eval_passed=eval_passed,
            user_confirmed=user_confirmed,
        )
        if action == "reject":
            proposal.status = "rejected"
            self.proposals.save(proposal)
            self.proposals.log.write(
                "apply_rejected",
                {"proposal_uid": proposal.proposal_uid, "reason": proposal.reject_reason or "gate"},
            )
            return None
        if action == "await_user":
            proposal.status = "awaiting_approval"
            self.proposals.save(proposal)
            return None

        # auto_apply path (E2 V0/V1 or user-confirmed)
        tkey = _target_key(proposal)
        if proposal.proposal_uid in self._rollback_blocked or proposal.pattern_key in self._rollback_blocked:
            proposal.status = "rejected"
            proposal.reject_reason = "blocked_after_rollback"
            self.proposals.save(proposal)
            return None

        rel = proposal.target.get("file") or "schema/TOOLS.md"
        # Never write into freeze source trees
        if rel.startswith("localwiki/") or rel.startswith("evals/"):
            proposal.status = "rejected"
            proposal.reject_reason = "freeze_zone"
            self.proposals.save(proposal)
            return None

        path = self.root / rel
        change = _apply_diff_to_file(path, proposal.diff)
        vid = self.registry.next_version_id()
        auto = self.level == "E2" and str(proposal.risk_tier).upper() in ("V0", "V1") and not user_confirmed
        ver = EvolutionVersion(
            version=vid,
            proposal_uid=proposal.proposal_uid,
            created_at=_now_iso(),
            changes=[change],
            auto_applied=auto or (self.level == "E2" and str(proposal.risk_tier).upper() in ("V0", "V1")),
            metrics_baseline=dict(metrics_baseline or {"task_success": 1.0, "tool_error_rate": 0.0}),
        )
        # For user-confirmed apply, still mark auto_applied False
        if user_confirmed:
            ver.auto_applied = False
        elif self.level == "E2" and str(proposal.risk_tier).upper() in ("V0", "V1"):
            ver.auto_applied = True

        self.registry.append_version(ver)
        frozen = self.registry.record_target_edit(tkey)
        proposal.status = "monitoring"
        proposal.evolution_version_base = vid
        self.proposals.save(proposal)

        mon = MonitorState(
            version=vid,
            proposal_uid=proposal.proposal_uid,
            started_at=_now_iso(),
            related_tasks_seen=0,
            metrics=dict(ver.metrics_baseline),
            baseline_metrics=dict(ver.metrics_baseline),
        )
        self.registry.save_monitor(mon)
        self.proposals.log.write(
            "applied",
            {
                "proposal_uid": proposal.proposal_uid,
                "version": vid,
                "auto_applied": ver.auto_applied,
                "oscillation_frozen": frozen,
            },
        )
        return ver

    def observe_task(
        self,
        *,
        metrics: dict[str, float],
        related: bool = True,
    ) -> EvolutionVersion | None:
        """Update monitor; auto-rollback E2 auto-applied on regression."""
        mon = self.registry.load_monitor()
        if mon is None or mon.closed:
            return None
        if related:
            mon.related_tasks_seen += 1
        mon.metrics.update(metrics)
        self.registry.save_monitor(mon)

        # Check window end
        started = datetime.fromisoformat(mon.started_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        window_done = mon.related_tasks_seen >= MONITOR_TASK_LIMIT or (
            _now() - started
        ) >= timedelta(days=MONITOR_DAY_LIMIT)

        regressed = self._metrics_regressed(mon.baseline_metrics, mon.metrics)
        if regressed:
            return self.rollback(mon.version, reason="monitor_regression", automatic=True)

        if window_done:
            mon.closed = True
            self.registry.save_monitor(mon)
            # mark stable
            for p in self.proposals.list_proposals():
                if p.proposal_uid == mon.proposal_uid:
                    p.status = "stable"
                    self.proposals.save(p)
                    break
        return None

    def _metrics_regressed(
        self,
        baseline: dict[str, float],
        current: dict[str, float],
    ) -> bool:
        # Higher is better for task_success / score; lower better for *_rate / *_error*
        for k, b in baseline.items():
            c = float(current.get(k, b))
            if re.search(r"error|rate|latency", k, re.I):
                if c > b + REGRESSION_THRESHOLD:
                    return True
            else:
                if c < b - REGRESSION_THRESHOLD:
                    return True
        return False

    def rollback(
        self,
        version: str,
        *,
        reason: str = "manual",
        automatic: bool = False,
    ) -> EvolutionVersion | None:
        versions = self.registry.list_versions()
        target = next((v for v in versions if v.get("version") == version), None)
        if not target:
            return None

        # V2 auto-applied should not happen; if automatic and not auto_applied → rollback proposal instead
        if automatic and not target.get("auto_applied"):
            # Generate rollback proposal for user (V2 path)
            rb = ImprovementProposal(
                proposal_uid=f"imp_rb_{version}",
                kind="rollback",
                target={"file": "evolution/rollback", "anchor": version},
                diff=f"+ revert {version}",
                rationale=f"monitor requested rollback: {reason}",
                risk_tier="V2",
                status="awaiting_approval",
                evolution_version_base=version,
                rollback={"type": "revert_to", "version": version},
            )
            self.proposals.save(rb)
            self.proposals.log.write(
                "rollback_proposal",
                {"version": version, "reason": reason},
            )
            return None

        # Restore file contents from recorded hashes by re-reading before from versions file
        # We stored only hashes in jsonl; keep parallel snapshot files
        snap_dir = self.dir_snapshots()
        for ch in target.get("changes") or []:
            path = Path(ch["path"])
            before_file = snap_dir / f"{version}_{_sha256_text(ch['path'])[:8]}.before"
            if before_file.exists():
                path.write_text(before_file.read_text(encoding="utf-8"), encoding="utf-8")
            elif path.exists():
                # Best-effort: strip last learned-rules append
                text = path.read_text(encoding="utf-8")
                text2 = re.sub(r"\n## 已学习的规则\n[\s\S]*\Z", "\n", text)
                path.write_text(text2, encoding="utf-8")

        new_id = self.registry.next_version_id()
        rb_ver = EvolutionVersion(
            version=new_id,
            proposal_uid=str(target.get("proposal_uid") or ""),
            created_at=_now_iso(),
            changes=[],
            auto_applied=False,
            rolled_back_from=version,
            status="active",
        )
        self.registry.append_version(rb_ver)
        self._rollback_blocked.add(str(target.get("proposal_uid") or ""))
        # Also block pattern re-proposal via log learning
        self.proposals.log.write(
            "rolled_back",
            {"from": version, "to": new_id, "reason": reason, "automatic": automatic},
        )
        mon = self.registry.load_monitor()
        if mon and mon.version == version:
            mon.closed = True
            self.registry.save_monitor(mon)

        # Write LRN-like note into evolution log to avoid re-proposing
        learnings = self.root / ".kw" / "learnings" / "LEARNINGS.md"
        learnings.parent.mkdir(parents=True, exist_ok=True)
        if not learnings.exists():
            learnings.write_text("# LEARNINGS\n\n", encoding="utf-8")
        with learnings.open("a", encoding="utf-8") as f:
            f.write(
                f"\n## [LRN-ROLLBACK-{version}] correction\n\n"
                f"**Status**: resolved\n\n### Summary\n"
                f"Do not re-propose improvement {target.get('proposal_uid')} "
                f"(rolled back: {reason})\n"
            )
        return rb_ver

    def dir_snapshots(self) -> Path:
        d = self.registry.dir / "snapshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def apply_with_snapshots(
        self,
        proposal: ImprovementProposal,
        **kwargs: Any,
    ) -> EvolutionVersion | None:
        """Apply and persist before-text snapshots for faithful rollback."""
        rel = proposal.target.get("file") or "schema/TOOLS.md"
        path = self.root / rel
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        ver = self.apply(proposal, **kwargs)
        if ver is None:
            return None
        snap = self.dir_snapshots() / f"{ver.version}_{_sha256_text(str(path))[:8]}.before"
        snap.write_text(before, encoding="utf-8")
        return ver


# Explicit matrix for tests (AT-EVO-07)
GATE_MATRIX: dict[tuple[str, str], GateAction] = {
    ("E1", "V0"): "await_user",
    ("E1", "V1"): "await_user",
    ("E1", "V2"): "await_user",
    ("E2", "V0"): "auto_apply",
    ("E2", "V1"): "auto_apply",
    ("E2", "V2"): "await_user",
}
