"""Rewrite user-facing openable URLs in assistant text (R3.26 — generic, not demo-only)."""

from __future__ import annotations

import re

# After loopback host ":", a non-numeric port token → replace with real port.
_LOOPBACK_HOST = r"(?:127\.0\.0\.1|localhost|\[::1\])"
_PORT_PLACEHOLDER = (
    r"(?:端口|<[^>]*端口[^>]*>|\{port\}|\{PORT\}|PORT|LocalWiki端口|"
    r"<LocalWiki端口>|<LocalWiki\s*port>)"
)
_RE_LOOPBACK_BAD_PORT = re.compile(
    rf"(https?://{_LOOPBACK_HOST}:)({_PORT_PLACEHOLDER})(/|\s|$|[\"'）\)\]》])",
    re.I,
)
# Also catch bare "127.0.0.1:端口" without scheme
_RE_BARE_BAD_PORT = re.compile(
    rf"((?:^|[^\w/]){_LOOPBACK_HOST}:)({_PORT_PLACEHOLDER})(/|\s|$|[\"'）\)\]》])",
    re.I | re.M,
)


def rewrite_openable_urls(text: str, *, port: int) -> str:
    """Replace non-numeric loopback port placeholders with ``port`` (idempotent)."""
    s = text or ""
    if not s:
        return s
    p = str(int(port))

    def _sub(m: re.Match[str]) -> str:
        return f"{m.group(1)}{p}{m.group(3)}"

    s = _RE_LOOPBACK_BAD_PORT.sub(_sub, s)
    s = _RE_BARE_BAD_PORT.sub(_sub, s)
    return s
