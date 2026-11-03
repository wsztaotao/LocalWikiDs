---
name: knowledge-ingest
description: >
  LocalWiki knowledge compile skill (R3.28 K7). Use when ingesting a source into the wiki:
  extract main content structure, summary, key points, quotes, and relations to existing pages.
license: MIT
compatibility: LocalWiki knowledge_compile route; works offline with routed LLM.
metadata:
  author: LocalWiki
  version: "1.0.0"
  epic: R3.28
---

# knowledge-ingest

Turn one extracted source document into durable wiki knowledge for LocalWiki.

## Goals (must cover)

1. **Main content** — What the material is about; outline / structure of arguments.
2. **Summary** — Short abstract (Chinese preferred if source is Chinese).
3. **Key points** — Atomic core claims (not fluff).
4. **Quotes** — Important verbatim excerpts with a one-line note on why they matter.
5. **Relations** — Links to existing wiki pages (补充 / 矛盾 / 延伸); use `[[wikilink]]` paths when known.
6. **Dedup awareness** — If an existing page already covers the same topic, prefer updating/relating over inventing a near-duplicate title.

## Workflow

1. Read the full source text (or truncated body provided).
2. Read the **existing wiki titles** list injected by LocalWiki (if any).
3. Produce **ONLY JSON** matching the contract below (no markdown fences unless unavoidable).
4. Prefer few high-quality topic pages over many thin pages.
5. Every topic page body should include sections: `## 核心观点`, `## 引用`, `## 相关` when applicable.

## JSON contract

```json
{
  "summary": "短摘要",
  "main_content": "主要内容/结构概述",
  "key_points": ["核心观点1", "核心观点2"],
  "quotes": [{"text": "原文摘录", "note": "为何重要"}],
  "related": [
    {"title": "已有页标题", "relation": "补充|矛盾|延伸", "wikilink": "topics/example"}
  ],
  "pages": [
    {
      "title": "主题标题",
      "page_type": "Topic",
      "summary": "一句话",
      "body": "markdown 正文"
    }
  ]
}
```

Rules:

- `pages` must contain at least one page.
- `summary` and `key_points` are required (`key_points` may be empty only for extremely short sources; explain in `main_content`).
- Do not invent download URLs or claim tools are missing.
- Do not paste internal agent dumps.

## Heuristic / weak path

When no LLM is used, LocalWiki still fills the same field names from deterministic extraction; prefer the same section headings so UI and gates stay consistent.
