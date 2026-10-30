"""User-turn intent routing for LocalWiki ↔ OpenCode glue (R3.23 / R3.25)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from localwiki.runtime.export_intent import export_intent, is_long_form

IntentKind = Literal["demo", "export_short", "export_long", "chat"]

_DUMP_MARKERS = (
    "Continue if you have next steps",
    "## Objective",
    "## Next Move",
    "## Work State",
)
_DEMO_ASSET_RE = re.compile(
    r"""(?:href|src)\s*=\s*["']([^"']+\.(?:css|js))["']""",
    re.I,
)

_DEMO_RE = re.compile(
    r"(看到页面|打开看看|打开页面|演示|跑起来|可打开地址|"
    r"视觉冲击|首页|落地页|landing\s*page|"
    r"http\.?server|预览|"
    r"show\s+(me\s+)?(the\s+)?(page|demo|result)|"
    r"open\s+(the\s+)?(page|demo)|"
    r"see\s+(the\s+)?(page|result))",
    re.I,
)
_DEMO_HTML_RE = re.compile(
    r"(html\s*首页|彩色\s*html|写.*(html|网页).*(打开|看到|演示)|"
    r"\.kw/demo|"
    r"/demo/)",
    re.I,
)
_PREVIEW_OK_RE = re.compile(
    r"(?:https?://127\.0\.0\.1(?::\d+)?)?/demo/[A-Za-z0-9._\-/]+|"
    r"https?://127\.0\.0\.1:\d+/demo/?|"
    r"/demo/(?:index\.html)?",
    re.I,
)


def classify_user_intent(text: str) -> IntentKind:
    """Classify a user turn. Order: demo → export_long → export_short → chat."""
    s = (text or "").strip()
    if not s:
        return "chat"
    if _DEMO_RE.search(s) or _DEMO_HTML_RE.search(s):
        # Pure export with incidental "打开" download wording still export if
        # clearly Word/PDF-only and no HTML/demo path — but demo wins when
        # demo markers present (spec: demo before export).
        return "demo"
    if export_intent(s):
        return "export_long" if is_long_form(s) else "export_short"
    return "chat"


def demo_preview_ok(text: str) -> bool:
    """True when assistant text already points at LocalWiki /demo/ preview."""
    return bool(_PREVIEW_OK_RE.search(text or ""))


def demo_asset_gaps(workspace: Path | str) -> list[str]:
    """Return missing demo files relative to ``.kw/demo`` (empty if complete)."""
    root = Path(workspace).resolve()
    demo = root / ".kw" / "demo"
    index = demo / "index.html"
    missing: list[str] = []
    if not index.is_file():
        return [".kw/demo/index.html"]
    try:
        html = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [".kw/demo/index.html"]
    for m in _DEMO_ASSET_RE.finditer(html):
        rel = (m.group(1) or "").strip().replace("\\", "/")
        if not rel or rel.startswith(("http://", "https://", "//", "data:")):
            continue
        # strip query/hash
        rel = rel.split("?", 1)[0].split("#", 1)[0]
        if rel.startswith("/"):
            # only honor /demo/... style
            if rel.startswith("/demo/"):
                candidate = root / ".kw" / "demo" / rel[len("/demo/") :]
            else:
                continue
        else:
            candidate = (demo / rel).resolve()
            try:
                candidate.relative_to(demo.resolve())
            except ValueError:
                continue
        if not candidate.is_file():
            missing.append(str(Path(".kw/demo") / Path(rel).name if not rel.startswith("/") else rel))
            # prefer path under demo
            try:
                missing[-1] = str(candidate.relative_to(root)).replace("\\", "/")
            except ValueError:
                pass
    # dedupe
    seen: set[str] = set()
    out: list[str] = []
    for x in missing:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def demo_delivered(workspace: Path | str, reply_text: str) -> bool:
    """Preview link present and demo index (+ referenced css/js) on disk."""
    if not demo_preview_ok(reply_text):
        return False
    return not demo_asset_gaps(workspace)


def looks_like_oc_dump(text: str) -> bool:
    s = text or ""
    return any(m in s for m in _DUMP_MARKERS)


def synthesize_incomplete_reply(
    *,
    error: dict | None = None,
    present: list[str] | None = None,
    missing: list[str] | None = None,
    preview_ok: bool = False,
    can_continue: bool = False,
    cancel_reason: str | None = None,
) -> str:
    """User-facing Chinese summary instead of OC internal dump."""
    reason = (cancel_reason or "").strip()
    if reason == "demo_bash_denied":
        lines = ["已拦截删除/清空 `.kw/demo` 的危险命令，任务已停止。"]
    elif reason == "demo_spin":
        lines = [
            "检测到 OpenCode 长时间无交付进展（空转），已按执行状况停止。",
            "不是固定超时，而是连续工具轮次没有补齐 demo 文件。",
        ]
    elif reason == "user":
        lines = ["任务已停止。"]
    else:
        lines = ["任务尚未完整交付（已过滤内部过程纪要）。"]
    if error and isinstance(error, dict):
        code = error.get("code") or ""
        msg = error.get("message") or ""
        if code or msg:
            lines.append(f"状态：{code} {msg}".strip())
    if present:
        lines.append("已落盘：" + "、".join(present[:12]))
    if missing:
        lines.append("仍缺失：" + "、".join(missing[:12]))
    if preview_ok or present:
        lines.append(
            "若 index 已在，可尝试打开：`/demo/index.html`"
            if not preview_ok
            else "预览可用：是 — `/demo/index.html`"
        )
    else:
        lines.append("预览可用：否")
    if can_continue and reason not in ("demo_bash_denied",):
        lines.append("可点工作台「继续」让 OpenCode 接着补齐缺失文件（禁止再删 demo）。")
    return "\n".join(lines)


def should_strip_export_history(kind: IntentKind) -> bool:
    return kind not in ("export_short", "export_long")


def demo_nudge_prompt(original_user: str, *, spin: bool = False) -> str:
    import os

    user = (original_user or "").strip()
    port = (os.environ.get("LOCALWIKI_PORT") or "8765").strip() or "8765"
    spin_line = (
        "【空转催促】上一轮工具多为 read/todowrite/无效 bash，没有补齐 demo 交付。\n"
        if spin
        else ""
    )
    return (
        f"{spin_line}"
        "【系统催促 — 演示预览】上一轮没有给出可打开的 LocalWiki 预览链接，"
        "或 `.kw/demo/` 文件不完整。\n"
        "请用 **lw_write**（大文件优先）把页面写入工作区 `.kw/demo/`。"
        "复杂页优先 **单个 index.html 内联 CSS/JS**，减少多文件写删。\n"
        "只补仍然缺失的文件；**禁止** rm/del/Remove-Item/清空已有 `.kw/demo/**`。\n"
        "并在回复中给出可打开地址：优先 `/demo/index.html`，"
        f"或 `http://127.0.0.1:{port}/demo/index.html`（必须用数字端口，禁止写「端口」占位）。"
        "不要另起 http.server。\n"
        "禁止只贴 pip/Flask 安装教程而无预览链接；"
        "禁止推销 `/api/exports/` 或 Word/PDF；"
        "禁止把 Objective / Next Move 等内部纪要当作用户回复。\n\n"
        f"【原用户请求】\n{user}"
    )
