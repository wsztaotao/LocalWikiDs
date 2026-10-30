"""Wiki directory categories, front-matter categories, and title clusters (R3.24)."""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from localwiki.llmwiki.lint import parse_front_matter

DIR_CATEGORY_LABELS: dict[str, str] = {
    "topics": "主题",
    "concepts": "概念",
    "entities": "实体",
    "decisions": "决策",
    "questions": "问题",
    "references": "来源",
    "memories": "记忆",
    "experiences": "经验",
    "profiles": "风格/档案",
    "other": "其他",
}

_DIR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("topics/", "topics"),
    ("concepts/", "concepts"),
    ("entities/", "entities"),
    ("decisions/", "decisions"),
    ("questions/", "questions"),
    ("references/", "references"),
    ("memories/", "memories"),
    ("experiences/", "experiences"),
    ("profiles/", "profiles"),
)

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
MAX_CATEGORIES_PER_PAGE = 16
MAX_CATEGORY_LEN = 32


def dir_category(path: str) -> str:
    p = (path or "").replace("\\", "/").lstrip("/")
    if p.startswith("references/assets/"):
        return "references"
    for pref, cid in _DIR_PREFIXES:
        if p.startswith(pref):
            return cid
    return "other"


def category_label(category_id: str) -> str:
    if category_id.startswith("custom:"):
        return category_id[len("custom:") :] or "自定义"
    return DIR_CATEGORY_LABELS.get(category_id, category_id)


def normalize_category_tag(raw: str) -> str | None:
    t = (raw or "").strip()
    if not t:
        return None
    t = t.replace(":", "·")[:MAX_CATEGORY_LEN].strip()
    return t or None


def parse_page_categories(raw: str | None) -> list[str]:
    if not raw:
        return []
    meta, _ = parse_front_matter(raw)
    val = meta.get("categories")
    out: list[str] = []
    seen: set[str] = set()
    items: list[Any]
    if isinstance(val, list):
        items = val
    elif isinstance(val, str):
        items = [x.strip() for x in val.split(",")]
    else:
        items = []
    for it in items:
        tag = normalize_category_tag(str(it))
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= MAX_CATEGORIES_PER_PAGE:
            break
    return out


def validate_categories(cats: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in cats:
        tag = normalize_category_tag(str(c))
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= MAX_CATEGORIES_PER_PAGE:
            break
    return out


def _token_set(text: str) -> set[str]:
    text = (text or "").lower()
    out: set[str] = set()
    for m in re.finditer(r"[\u4e00-\u9fff]+|[a-zA-Z]{3,}|\d+", text):
        chunk = m.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) == 1:
                out.add(chunk)
            else:
                for i in range(len(chunk) - 1):
                    out.add(chunk[i : i + 2])
        else:
            out.add(chunk)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _norm_title(text: str) -> str:
    return _PUNCT.sub("", (text or "").strip().lower())


def _common_prefix(a: str, b: str) -> str:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return a[:i]


def _prefix_ok(pref: str) -> bool:
    if not pref:
        return False
    # ≥2 CJK or ≥4 latin/digits
    cjk = len(re.findall(r"[\u4e00-\u9fff]", pref))
    if cjk >= 2:
        return True
    latin = re.sub(r"[^a-zA-Z0-9]", "", pref)
    return len(latin) >= 4


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster_titles(
    items: Sequence[dict[str, Any]],
    *,
    jaccard_threshold: float = 0.45,
) -> dict[str, str]:
    """Return path -> cluster_id within one dir group. Singleton → _ungrouped."""
    if not items:
        return {}
    n = len(items)
    uf = _UF(n)
    norms = [_norm_title(str(it.get("title") or "")) for it in items]
    tokens = [_token_set(str(it.get("title") or "")) for it in items]

    for i in range(n):
        for j in range(i + 1, n):
            pref = _common_prefix(norms[i], norms[j])
            if _prefix_ok(pref) or _jaccard(tokens[i], tokens[j]) >= jaccard_threshold:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    out: dict[str, str] = {}
    for root, idxs in groups.items():
        if len(idxs) < 2:
            for i in idxs:
                out[str(items[i]["path"])] = "_ungrouped"
            continue
        # cluster name = longest common prefix of norms, else top bigram
        prefs = [norms[i] for i in idxs if norms[i]]
        name = prefs[0] if prefs else "cluster"
        for p in prefs[1:]:
            name = _common_prefix(name, p)
        if not _prefix_ok(name):
            # highest frequency bigram across titles
            freq: dict[str, int] = {}
            for i in idxs:
                for t in tokens[i]:
                    if len(t) >= 2:
                        freq[t] = freq.get(t, 0) + 1
            name = max(freq, key=lambda k: (freq[k], k), default="cluster")
        cid = name or "cluster"
        for i in idxs:
            out[str(items[i]["path"])] = cid
    return out


def set_categories_in_content(raw: str, categories: Sequence[str]) -> str:
    """Rewrite or insert categories in YAML front matter."""
    cats = validate_categories(categories)
    # YAML list inline
    yaml_list = "[" + ", ".join(f'"{c}"' for c in cats) + "]"
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", raw or "", re.S)
    if not m:
        # prepend FM
        title_line = ""
        body = raw or ""
        return (
            f"---\nokf_version: \"0.2\"\ncategories: {yaml_list}\n---\n\n{body.lstrip()}"
        )
    yaml_text = m.group(1)
    body = raw[m.end() :]
    lines = yaml_text.splitlines()
    out_lines: list[str] = []
    replaced = False
    for line in lines:
        if re.match(r"^categories\s*:", line):
            out_lines.append(f"categories: {yaml_list}")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(f"categories: {yaml_list}")
    return "---\n" + "\n".join(out_lines) + "\n---\n" + body
