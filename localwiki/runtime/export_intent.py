"""Detect export intent and whether OpenCode delivered export files (R3.16–R3.21)."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

_EXPORT_RE = re.compile(
    r"(word|\.docx|docx|excel|\.xlsx|导出|下载|音频|语音|朗读|tts|mp3|\.mp3|speech|audio|pdf|\.pdf)",
    re.I,
)
_DOCX_INTENT_RE = re.compile(r"(word|\.docx|docx|excel|\.xlsx)", re.I)
_AUDIO_INTENT_RE = re.compile(r"(音频|语音|朗读|tts|mp3|\.mp3|speech|audio)", re.I)
_PDF_INTENT_RE = re.compile(r"(pdf|\.pdf)", re.I)
_LINK_RE = re.compile(r"/api/exports/([A-Za-z0-9._-]+)")
_CHAR_COUNT_RE = re.compile(
    r"(?:约|大约|大概)?\s*(\d{3,5})\s*字",
)
_LONG_FORM_KW_RE = re.compile(r"(长文|文章|作文|论文|完整稿)", re.I)
_TEXT_THEN_EXPORT_RE = re.compile(
    r"(先看正文|先写出来|先写正文|先出正文|再导出|然后再导出)",
    re.I,
)
_EXPORT_PROMO_RE = re.compile(
    r"(?:/api/exports/[A-Za-z0-9._-]+|"
    r"(?:点击)?(?:下载|打开)\s*(?:Word|PDF|音频|docx|pdf|mp3)|"
    r"Word\s*(?:文档|文件)|PDF\s*(?:文件|下载))",
    re.I,
)


def export_intent(text: str) -> bool:
    return bool(_EXPORT_RE.search(text or ""))


def wants_docx(text: str) -> bool:
    return bool(_DOCX_INTENT_RE.search(text or ""))


def wants_audio(text: str) -> bool:
    return bool(_AUDIO_INTENT_RE.search(text or ""))


def wants_pdf(text: str) -> bool:
    return bool(_PDF_INTENT_RE.search(text or ""))


def is_long_form(text: str) -> bool:
    """True when user wants a substantial article before/with export (R3.21 Q3=C)."""
    s = (text or "").strip()
    if not s:
        return False
    if _TEXT_THEN_EXPORT_RE.search(s):
        return True
    for m in _CHAR_COUNT_RE.finditer(s):
        try:
            if int(m.group(1)) >= 800:
                return True
        except ValueError:
            continue
    if _LONG_FORM_KW_RE.search(s) and export_intent(s):
        return True
    return False


def strip_export_promo(text: str) -> str:
    """Remove export download links / promo lines from history snippets (U2)."""
    if not text:
        return ""
    lines: list[str] = []
    for line in str(text).splitlines():
        if _LINK_RE.search(line) or _EXPORT_PROMO_RE.search(line):
            continue
        lines.append(line)
    out = "\n".join(lines).strip()
    # Also strip inline leftover links if a single-line blob
    out = _LINK_RE.sub("", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _exports_newer_than(workspace: Path, since: float) -> list[str]:
    root = Path(workspace).resolve() / ".kw" / "exports"
    if not root.is_dir():
        return []
    names: list[str] = []
    for p in root.iterdir():
        if not p.is_file():
            continue
        try:
            if p.stat().st_mtime >= since - 1.0:
                names.append(p.name)
        except OSError:
            continue
    return names


def _has_ext(names: list[str], *exts: str) -> bool:
    lower = [n.lower() for n in names]
    return any(any(n.endswith(e) for e in exts) for n in lower)


def export_delivered(
    workspace: Path | str,
    *,
    result: dict[str, Any] | None,
    out_text: str,
    job_started: float,
    user_text: str = "",
) -> bool:
    """True when requested export kinds have hard file/link evidence."""
    root = Path(workspace).resolve()
    newer = _exports_newer_than(root, job_started)

    from localwiki.exports import resolve_export_existing

    linked: list[str] = []
    for name in _LINK_RE.findall(out_text or ""):
        if resolve_export_existing(root, name) is not None:
            linked.append(name)
    evidence = list(dict.fromkeys([*newer, *linked]))
    if not evidence:
        # Tool events alone are not enough (OC may mark status completed on soft fail).
        return False

    need_docx = wants_docx(user_text) if user_text else False
    need_audio = wants_audio(user_text) if user_text else False
    need_pdf = wants_pdf(user_text) if user_text else False
    if not need_docx and not need_audio and not need_pdf:
        # Generic export intent (e.g. 导出/下载 alone): any new export file counts.
        return True
    ok = True
    if need_docx:
        ok = ok and _has_ext(evidence, ".docx", ".xlsx")
    if need_audio:
        ok = ok and _has_ext(evidence, ".mp3", ".wav")
    if need_pdf:
        ok = ok and _has_ext(evidence, ".pdf")
    return ok


def nudge_prompt(original_user: str, *, long_form: bool | None = None) -> str:
    """Retry prompt after a missed export; short vs long-form diverge (R3.21)."""
    user = (original_user or "").strip()
    if long_form is None:
        long_form = is_long_form(user)
    common_tail = (
        "禁止声称缺少工具或「未安装 Python」。\n"
        "成功后回复中必须包含真实链接 /api/exports/<file>.docx / .mp3 / .pdf（按用户需要）。\n\n"
        f"【原用户请求】\n{user}"
    )
    if long_form:
        return (
            "【系统催促 — 长文导出】上一轮没有把所需文件写入 .kw/exports。\n"
            "若聊天里还没有完整可见正文：先补写完整正文（不要空气泡），"
            "然后再调用 export_docx / export_tts / export_pdf"
            "（或 bash：`python -m localwiki.tools.oc_export_cli docx|tts|pdf ...`）。\n"
            "禁止跳过正文直接只调工具；"
            + common_tail
        )
    return (
        "【系统催促 — 短导出】上一轮没有把所需文件写入 .kw/exports。\n"
        "请立刻调用 OpenCode 工具 export_docx / export_tts / export_pdf"
        "（或 bash 执行 `python -m localwiki.tools.oc_export_cli docx|tts|pdf ...`）。\n"
        "可用一两句说明，但必须调用工具写出文件；禁止只回复正文而不导出。\n"
        + common_tail
    )


def now() -> float:
    return time.time()
