#!/usr/bin/env python3
"""LocalWiki v0.12 Cursor task queue runner.

Commands:
  next          Print the next ready task as JSON (topo-sorted by depends_on).
  list          List all tasks with status.
  start ID      Mark task as in_progress.
  complete ID   Mark task completed; optional --note.
  fail ID       Mark task failed; requires --note.
  block ID      Mark task blocked; requires --note.
  skip ID       Mark task skipped; requires --note.
  prompt ID     Print the full Cursor agent prompt for a task.
  ready         Print IDs of all currently ready tasks.
  graph         Print dependency order (IDs only).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve_paths() -> tuple[Path, Path]:
    """Allow remaining-queue via env LW_QUEUE / LW_STATE."""
    import os

    q = os.environ.get("LW_QUEUE")
    s = os.environ.get("LW_STATE")
    queue_path = Path(q) if q else ROOT / "docs" / "cursor-tasks" / "queue.json"
    state_path = Path(s) if s else ROOT / "docs" / "cursor-tasks" / "state.json"
    if not queue_path.is_absolute():
        queue_path = ROOT / queue_path
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    return queue_path, state_path


QUEUE_PATH, STATE_PATH = _resolve_paths()

KIND_MAP = {"fail": "failed", "block": "blocked", "skip": "skipped"}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_queue() -> dict:
    return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "version": 1,
            "updated_at": None,
            "current_task": None,
            "status": "idle",
            "completed": [],
            "failed": {},
            "blocked": {},
            "skipped": {},
            "history": [],
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    state["updated_at"] = utcnow()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def task_map(queue: dict) -> dict[str, dict]:
    return {t["id"]: t for t in queue["tasks"]}


def task_status(state: dict, task_id: str) -> str:
    if state.get("current_task") == task_id and state.get("status") == "in_progress":
        return "in_progress"
    if task_id in state.get("completed", []):
        return "completed"
    if task_id in state.get("failed", {}):
        return "failed"
    if task_id in state.get("blocked", {}):
        return "blocked"
    if task_id in state.get("skipped", {}):
        return "skipped"
    return "pending"


def is_satisfied(dep: str, state: dict) -> bool:
    return dep in state.get("completed", []) or dep in state.get("skipped", {})


def ready_tasks(queue: dict, state: dict) -> list[dict]:
    if state.get("status") == "in_progress" and state.get("current_task"):
        return []
    doneish = set(state.get("completed", [])) | set(state.get("skipped", {}))
    blocked_failed = set(state.get("blocked", {})) | set(state.get("failed", {}))
    out: list[dict] = []
    for t in queue["tasks"]:
        tid = t["id"]
        if tid in doneish or tid in blocked_failed:
            continue
        if all(is_satisfied(d, state) for d in t.get("depends_on", [])):
            out.append(t)
    return out


def topo_ids(queue: dict) -> list[str]:
    tasks = queue["tasks"]
    ids = [t["id"] for t in tasks]
    index = {tid: i for i, tid in enumerate(ids)}
    indeg = {tid: 0 for tid in ids}
    children: dict[str, list[str]] = {tid: [] for tid in ids}
    for t in tasks:
        for d in t.get("depends_on", []):
            if d not in indeg:
                raise SystemExit(f"Unknown dependency {d!r} for {t['id']}")
            indeg[t["id"]] += 1
            children[d].append(t["id"])
    ready = sorted([tid for tid, n in indeg.items() if n == 0], key=lambda x: index[x])
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for c in sorted(children[n], key=lambda x: index[x]):
            indeg[c] -= 1
            if indeg[c] == 0:
                ready.append(c)
                ready.sort(key=lambda x: index[x])
    if len(order) != len(ids):
        raise SystemExit("Dependency cycle detected in queue.json")
    return order


def build_prompt(queue: dict, task: dict) -> str:
    files = ", ".join(task.get("files", [])) or "(none)"
    chapters = ", ".join(f"第 {c} 章/节" for c in task.get("chapters", [])) or "(见任务卡)"
    extra = task.get("prompt_extra", "")
    deliverable = task.get("deliverable", "")
    gate = task.get("gate", "")
    phase = task.get("phase", "")
    pdtd = ""
    if gate or phase in ("Plan", "Design", "Build", "Test"):
        pdtd = f"""
PDTD 约束（强制，见 .cursor/rules/localwiki-pdtd-loop.mdc）：
- 本任务阶段：{phase}
- 交付物：{deliverable or files}
- 门禁：{gate or "无（可在完成后按编排继续）"}
- Plan：不清楚就提问；确认后再写 intent.md
- Design：仅在 intent 已确认后写 spec.md
- Build：仅在 spec 已确认后写 plan.md；**仅在用户明确确认 plan.md 后才能改代码**
- Test：可另起子 Agent 跑测；失败必须反馈用户，禁止伪造成功
- 若 gate 需要用户确认：写完文档后 STOP，等待用户确认，再 complete
"""
    continue_rule = (
        "C. 若本任务带 user_confirm_* 门禁：写完后停止并请用户确认，不要自动 complete 后继续改码。\n"
        "D. 若无门禁且 complete 返回 next：可继续下一张；fail/block 则停止。"
        if gate
        else "C. 若任务完成且队列仍有就绪任务：可继续下一张（仍须遵守 PDTD；遇确认门禁必须停下）。\n"
        "D. 若 fail/block：停止自动链，写清阻断原因。"
    )
    return f"""你在开发 LocalWiki v0.12。需求基线是 {queue["baseline_doc"]}。

任务卡：{task["id"]} {task["title"]}
阶段：{task.get("phase") or task.get("epic") or "?"}
依赖：{", ".join(task.get("depends_on", [])) or "无"}
相关章节：{chapters}
主要文件：{files}
完成标准：{task["acceptance"]}
{pdtd}
执行规则（必须遵守）：
1. 阅读基线文档与本任务相关章节；先读 docs/pdtd/ 与 AGENTS.md。
2. 文档与代码不一致时记入 docs/DECISIONS.md，不擅自扩 scope。
3. 本次只完成 {task["id"]}。
4. 不得放宽安全默认值；自进化不得触及第 23.2 节冻结区。
5. 写入 wiki/ 必须经 CommitService（若尚未实现，仅在已确认的 plan 内创建它）。
6. 外部服务单测用录制响应；@live 无账号则 SKIPPED/BLOCKED。

{extra}

结束时必须：
A. 更新状态：设置 LW_QUEUE/LW_STATE 后 python scripts/lw_task_runner.py complete|fail|block {task["id"]} --note "..."
B. 追加 docs/PROGRESS.md
{continue_rule}
"""


def append_history(state: dict, event: str, task_id: str, note: str = "") -> None:
    state.setdefault("history", []).append(
        {"ts": utcnow(), "event": event, "task_id": task_id, "note": note}
    )


def cmd_next(queue: dict, state: dict) -> int:
    if state.get("status") == "in_progress" and state.get("current_task"):
        tid = state["current_task"]
        task = task_map(queue)[tid]
        print(json.dumps({"status": "in_progress", "task": task}, ensure_ascii=False, indent=2))
        return 0
    ready = ready_tasks(queue, state)
    if not ready:
        remaining = [
            t["id"]
            for t in queue["tasks"]
            if task_status(state, t["id"]) in ("pending", "failed", "blocked")
        ]
        print(
            json.dumps(
                {
                    "status": "idle" if not remaining else "blocked_or_done",
                    "task": None,
                    "remaining": remaining,
                    "completed_count": len(state.get("completed", [])),
                    "total": len(queue["tasks"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    order = {tid: i for i, tid in enumerate(topo_ids(queue))}
    ready.sort(key=lambda t: order[t["id"]])
    print(json.dumps({"status": "ready", "task": ready[0]}, ensure_ascii=False, indent=2))
    return 0


def cmd_list(queue: dict, state: dict) -> int:
    rows = []
    for tid in topo_ids(queue):
        t = task_map(queue)[tid]
        rows.append(
            {
                "id": tid,
                "phase": t["phase"],
                "title": t["title"],
                "status": task_status(state, tid),
                "depends_on": t.get("depends_on", []),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_start(queue: dict, state: dict, task_id: str) -> int:
    tmap = task_map(queue)
    if task_id not in tmap:
        print(f"Unknown task: {task_id}", file=sys.stderr)
        return 2
    ready_ids = {t["id"] for t in ready_tasks(queue, state)}
    if state.get("status") == "in_progress" and state.get("current_task") != task_id:
        print(f"Another task in progress: {state['current_task']}", file=sys.stderr)
        return 2
    if task_id not in ready_ids and state.get("current_task") != task_id:
        print(f"Task not ready: {task_id}", file=sys.stderr)
        return 2
    state["current_task"] = task_id
    state["status"] = "in_progress"
    append_history(state, "start", task_id)
    save_state(state)
    print(json.dumps({"started": task_id}, ensure_ascii=False))
    return 0


def cmd_complete(queue: dict, state: dict, task_id: str, note: str) -> int:
    tmap = task_map(queue)
    if task_id not in tmap:
        print(f"Unknown task: {task_id}", file=sys.stderr)
        return 2
    if task_id not in state.get("completed", []):
        state.setdefault("completed", []).append(task_id)
    state.get("failed", {}).pop(task_id, None)
    state.get("blocked", {}).pop(task_id, None)
    state.get("skipped", {}).pop(task_id, None)
    if state.get("current_task") == task_id:
        state["current_task"] = None
        state["status"] = "idle"
    append_history(state, "complete", task_id, note)
    save_state(state)
    nxt = ready_tasks(queue, state)
    print(
        json.dumps(
            {
                "completed": task_id,
                "next": nxt[0]["id"] if nxt else None,
                "completed_count": len(state["completed"]),
                "total": len(queue["tasks"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_mark(queue: dict, state: dict, task_id: str, kind: str, note: str) -> int:
    tmap = task_map(queue)
    if task_id not in tmap:
        print(f"Unknown task: {task_id}", file=sys.stderr)
        return 2
    if not note:
        print("--note is required", file=sys.stderr)
        return 2
    bucket = state.setdefault(kind, {})
    bucket[task_id] = {"note": note, "at": utcnow()}
    if task_id in state.get("completed", []):
        state["completed"] = [x for x in state["completed"] if x != task_id]
    if state.get("current_task") == task_id:
        state["current_task"] = None
        state["status"] = "idle"
    append_history(state, kind, task_id, note)
    save_state(state)
    print(json.dumps({kind: task_id, "note": note}, ensure_ascii=False, indent=2))
    return 0


def cmd_prompt(queue: dict, task_id: str) -> int:
    tmap = task_map(queue)
    if task_id not in tmap:
        print(f"Unknown task: {task_id}", file=sys.stderr)
        return 2
    print(build_prompt(queue, tmap[task_id]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalWiki Cursor task runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("next")
    sub.add_parser("list")
    sub.add_parser("ready")
    sub.add_parser("graph")
    p_start = sub.add_parser("start")
    p_start.add_argument("task_id")
    p_complete = sub.add_parser("complete")
    p_complete.add_argument("task_id")
    p_complete.add_argument("--note", default="")
    for name in ("fail", "block", "skip"):
        p = sub.add_parser(name)
        p.add_argument("task_id")
        p.add_argument("--note", required=True)
    p_prompt = sub.add_parser("prompt")
    p_prompt.add_argument("task_id")
    args = parser.parse_args()

    queue = load_queue()
    state = load_state()

    if args.cmd == "next":
        return cmd_next(queue, state)
    if args.cmd == "list":
        return cmd_list(queue, state)
    if args.cmd == "ready":
        print(json.dumps([t["id"] for t in ready_tasks(queue, state)], ensure_ascii=False))
        return 0
    if args.cmd == "graph":
        print("\n".join(topo_ids(queue)))
        return 0
    if args.cmd == "start":
        return cmd_start(queue, state, args.task_id)
    if args.cmd == "complete":
        return cmd_complete(queue, state, args.task_id, args.note)
    if args.cmd in KIND_MAP:
        return cmd_mark(queue, state, args.task_id, KIND_MAP[args.cmd], args.note)
    if args.cmd == "prompt":
        return cmd_prompt(queue, args.task_id)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
