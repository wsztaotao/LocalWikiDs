"""One-shot generator for LW-44 baseline suites (>=20 tasks / domain)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "evals"

DOMAINS = {
    "wiki_qa": {
        "prefix": "wiki",
        "prompt": "根据 Wiki 回答：{topic}",
        "answer": "引用资料说明{topic}",
    },
    "memory_recall": {
        "prefix": "mem",
        "prompt": "召回关于 {topic} 的记忆",
        "answer": "记忆命中:{topic}",
    },
    "experience_pack": {
        "prefix": "exp",
        "prompt": "为任务选择经验包：{topic}",
        "answer": "pack-{i:02d}",
        "format": "json",
    },
    "agent_tasks": {
        "prefix": "agt",
        "prompt": "智能体标准任务：{topic}",
        "answer": "计划完成:{topic}",
    },
    "artifacts": {
        "prefix": "art",
        "prompt": "生成成果：{topic}",
        "answer": "artifact_ok:{topic}",
    },
}

TOPICS = [
    "项目概述",
    "决策记录",
    "会议纪要",
    "术语表",
    "接口约定",
    "错误码",
    "发布清单",
    "回滚步骤",
    "权限模型",
    "数据字典",
    "测试计划",
    "验收标准",
    "风险清单",
    "依赖版本",
    "环境变量",
    "备份策略",
    "监控指标",
    "告警规则",
    "SLO定义",
    "值班手册",
]


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for domain, cfg in DOMAINS.items():
        ddir = ROOT / domain
        ddir.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for i in range(1, 21):
            topic = f"topic-{i:02d}-{TOPICS[i - 1]}"
            if cfg.get("format") == "json":
                ans = f"pack-{i:02d}"
                task = {
                    "id": f"{cfg['prefix']}-{i:02d}",
                    "domain": domain,
                    "prompt": cfg["prompt"].format(topic=topic),
                    "topic": topic,
                    "answer_format": "json",
                    "expected_contains": ans,
                    "canonical_answer": json.dumps(
                        {"selected": ans, "why": f"match {topic}"},
                        ensure_ascii=False,
                    ),
                    "checks": [
                        {"type": "json_schema_keys", "keys": ["selected", "why"]},
                    ],
                }
            else:
                ans = cfg["answer"].format(topic=topic, i=i)
                checks = [
                    {"type": "contains", "value": ans.split(":")[0] if ":" in ans else ans[:6]},
                ]
                if domain == "wiki_qa":
                    checks = [
                        {"type": "contains", "value": ans},
                        {"type": "not_contains", "value": "无依据捏造"},
                    ]
                elif domain == "memory_recall":
                    checks = [
                        {"type": "contains", "value": "记忆命中"},
                        {"type": "contains", "value": f"topic-{i:02d}"},
                    ]
                elif domain == "agent_tasks":
                    checks = [
                        {"type": "contains", "value": "计划完成"},
                        {"type": "regex", "value": r"计划完成:.+"},
                    ]
                elif domain == "artifacts":
                    checks = [
                        {"type": "contains", "value": "artifact_ok"},
                        {"type": "not_contains", "value": "TODO_UNFINISHED"},
                    ]
                task = {
                    "id": f"{cfg['prefix']}-{i:02d}",
                    "domain": domain,
                    "prompt": cfg["prompt"].format(topic=topic),
                    "topic": topic,
                    "expected_contains": ans,
                    "canonical_answer": ans,
                    "checks": checks,
                }
            lines.append(json.dumps(task, ensure_ascii=False))
        (ddir / "tasks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (ddir / "README.md").write_text(
            f"# {domain}\n\nFreeze-zone baseline suite (≥20 tasks). "
            "Not visible to proposal models.\n",
            encoding="utf-8",
        )
    (ROOT / "README.md").write_text(
        "# LocalWiki evals (freeze zone)\n\n"
        "Domains: wiki_qa, memory_recall, experience_pack, agent_tasks, artifacts.\n"
        "Each suite has ≥20 tasks. Evolution must not modify these files; "
        "proposal models must not read them.\n",
        encoding="utf-8",
    )
    print("generated", ROOT)


if __name__ == "__main__":
    main()
