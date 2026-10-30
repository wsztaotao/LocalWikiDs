"""Tests for Tavily adapter (LW-27 / §12.3 / AT-TOOL-04/05)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.tools.registry import build_default_registry  # noqa: E402
from localwiki.tools.tavily import (  # noqa: E402
    TavilyAdapter,
    bind_tavily_tools,
    build_tavily_adapter,
    check_tavily_privacy,
)


class FakeTavily:
    """Recorded-response stand-in for tavily.TavilyClient."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.searches: list[dict[str, Any]] = []
        self.extracts: list[Any] = []
        self.crawls: list[dict[str, Any]] = []

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.searches.append({"query": query, **kwargs})
        return {
            "results": [
                {"title": "Hit", "url": "https://example.com", "content": "snippet"}
            ]
        }

    def extract(self, urls: list[str] | str, **kwargs: Any) -> dict[str, Any]:
        self.extracts.append(urls)
        return {"results": [{"url": urls, "raw_content": "body"}]}

    def crawl(self, url: str, **kwargs: Any) -> dict[str, Any]:
        self.crawls.append({"url": url, **kwargs})
        return {"results": [{"url": url}]}

    def map(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return {"base_url": url, "urls": [url]}


def test_privacy_matrix() -> None:
    assert check_tavily_privacy(content_class="public")[0]
    assert check_tavily_privacy(content_class="normal")[0]
    ok, reason = check_tavily_privacy(content_class="private")
    assert not ok and reason and "needs_auth" in reason
    assert check_tavily_privacy(
        content_class="private", allow_private_in_query=True
    )[0]
    ok, reason = check_tavily_privacy(content_class="sensitive")
    assert not ok and reason and "forbidden" in reason
    # Cannot widen sensitive even with flag
    ok, _ = check_tavily_privacy(
        content_class="sensitive", allow_sensitive_in_query=True
    )
    assert not ok
    assert check_tavily_privacy(offline=True) == (False, "offline_mode")


def test_search_marks_untrusted_and_records_usage() -> None:
    fake = FakeTavily("k")
    adapter = TavilyAdapter(
        api_key="k",
        sdk_factory=lambda key: fake,
        budget=5,
        task_uid="t1",
    )
    out = adapter.search("localwiki design")
    assert out["ok"]
    assert out["untrusted"] is True
    assert "<untrusted>" in out["wrapped"]
    assert fake.searches[0]["query"] == "localwiki design"
    assert adapter.status()["used"] == 1
    events = adapter.usage_events()
    assert events[-1]["kind"] == "tavily"
    assert events[-1]["tool"] == "web_search"
    assert events[-1]["task_uid"] == "t1"


def test_budget_exceeded() -> None:
    fake = FakeTavily("k")
    adapter = TavilyAdapter(api_key="k", sdk_factory=lambda key: fake, budget=2)
    assert adapter.search("a")["ok"]
    assert adapter.search("b")["ok"]
    out = adapter.search("c")
    assert out["ok"] is False
    assert out["error"] == "budget_exceeded"
    assert len(fake.searches) == 2


def test_offline_blocks() -> None:
    fake = FakeTavily("k")
    adapter = TavilyAdapter(
        api_key="k", offline=True, sdk_factory=lambda key: fake
    )
    out = adapter.search("x")
    assert out["ok"] is False
    assert out["error"] == "offline_mode"
    assert fake.searches == []


def test_private_blocked_without_auth() -> None:
    fake = FakeTavily("k")
    adapter = TavilyAdapter(
        api_key="k",
        content_class="private",
        allow_private_in_query=False,
        sdk_factory=lambda key: fake,
    )
    out = adapter.search("my diary")
    assert out["ok"] is False
    assert "privacy" in out["error"]
    assert fake.searches == []


def test_not_configured_falls_back() -> None:
    calls: list[dict[str, Any]] = []

    def fb(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"ok": True, "results": [{"title": "legacy"}]}

    adapter = build_tavily_adapter(
        api_key=None,
        sdk_factory=lambda k: FakeTavily(k),
        fallback_search=fb,
    )
    st = adapter.status()
    assert st["available"] is False
    assert st["hint"] and "not_configured" in st["hint"]
    out = adapter.search("q")
    assert out["provider"] == "fallback"
    assert out["ok"]
    assert calls and calls[0]["query"] == "q"


def test_bind_registry_tools() -> None:
    fake = FakeTavily("k")
    adapter = TavilyAdapter(api_key="k", sdk_factory=lambda key: fake)
    reg = build_default_registry()
    bind_tavily_tools(reg, adapter)
    r = reg.execute("web_search", {"query": "hello"})
    assert r.ok
    assert r.output["provider"] == "tavily"
    assert reg.get("web_crawl") is not None
    c = reg.execute("web_crawl", {"url": "https://example.com", "limit": 3})
    assert c.ok
    assert c.output["limit"] == 3


@pytest.mark.live
def test_live_tavily_search_skipped_without_key() -> None:
    """@live: real account required — skip when secret missing."""
    import os

    key = os.environ.get("TAVILY_API_KEY") or os.environ.get("LOCALWIKI_TAVILY_KEY")
    if not key:
        pytest.skip("SKIPPED/@live: no Tavily API key")
    try:
        from tavily import TavilyClient
    except ImportError:
        site = ROOT / ".venv-work" / "Lib" / "site-packages"
        if site.is_dir():
            sys.path.insert(0, str(site))
        from tavily import TavilyClient

    client = TavilyClient(api_key=key)
    res = client.search("LocalWiki", max_results=1)
    assert res is not None
