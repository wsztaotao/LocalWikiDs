"""Strip Markdown-ish markup before writing export files (R3.18)."""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```[\w+-]*\r?\n?([\s\S]*?)```", re.M)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_HR_RE = re.compile(r"(?m)^\s*([-*_]){3,}\s*$")
_BQ_RE = re.compile(r"(?m)^\s*>\s?")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def strip_markdown_for_export(text: str) -> str:
    """Return plain text suitable for Word/PDF/TTS (no MD markers)."""
    s = text or ""
    s = s.replace("\r\n", "\n")
    s = _FENCE_RE.sub(lambda m: m.group(1).rstrip() + "\n", s)
    s = _IMAGE_RE.sub(lambda m: m.group(1) or "", s)
    s = _LINK_RE.sub(lambda m: m.group(1), s)
    s = _INLINE_CODE_RE.sub(lambda m: m.group(1), s)
    s = _HEADING_RE.sub("", s)
    s = _BOLD_RE.sub(lambda m: m.group(2), s)
    s = _STRIKE_RE.sub(lambda m: m.group(1), s)
    s = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _HR_RE.sub("", s)
    s = _BQ_RE.sub("", s)
    # leftover emphasis markers
    s = s.replace("**", "").replace("__", "")
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()
