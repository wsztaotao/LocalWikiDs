"""R3.27: dangerous demo bash guard + event-based spin tracker."""

from __future__ import annotations

from localwiki.runtime.demo_guard import (
    extract_bash_command,
    is_dangerous_demo_bash,
    tool_looks_like_bash,
)
from localwiki.runtime.intent_route import synthesize_incomplete_reply
from localwiki.runtime.oc_spin import DemoSpinTracker, SPIN_NUDGE_AFTER, SPIN_STOP_AFTER


def test_dangerous_demo_bash_posix_rm() -> None:
    assert is_dangerous_demo_bash("rm -rf .kw/demo/css/style.css")
    assert is_dangerous_demo_bash("rm .kw/demo/index.html")
    assert is_dangerous_demo_bash('rm -f "./.kw/demo/js/app.js"')


def test_dangerous_demo_bash_windows() -> None:
    assert is_dangerous_demo_bash(r"del .kw\demo\css\style.css")
    assert is_dangerous_demo_bash("Remove-Item -Recurse .kw/demo/css")
    assert is_dangerous_demo_bash("rd /s /q .kw\\demo\\js")


def test_safe_bash_not_blocked() -> None:
    assert not is_dangerous_demo_bash("mkdir -p .kw/demo/css")
    assert not is_dangerous_demo_bash("ls .kw/demo")
    assert not is_dangerous_demo_bash("rm -rf /tmp/other")
    assert not is_dangerous_demo_bash("python -m localwiki.tools.lw_write_cli ...")
    assert not is_dangerous_demo_bash("")


def test_extract_and_bash_name() -> None:
    assert tool_looks_like_bash("bash")
    assert tool_looks_like_bash("Shell")
    assert not tool_looks_like_bash("lw_write")
    assert (
        extract_bash_command({"title": "rm .kw/demo/x", "name": "bash"})
        == "rm .kw/demo/x"
    )
    assert (
        extract_bash_command({"state": {"input": {"command": "del .kw/demo/a"}}})
        == "del .kw/demo/a"
    )


def test_spin_nudge_then_stop() -> None:
    t = DemoSpinTracker(nudge_after=3, stop_after=5)
    for _ in range(3):
        t.note_tool_end("read", path_or_title="wiki/x.md")
    assert t.should_nudge()
    t.mark_nudge_sent()
    assert not t.should_nudge()
    assert not t.should_stop()
    t.note_tool_end("todowrite")
    t.note_tool_end("bash", path_or_title="ls")
    assert t.should_stop()


def test_spin_reset_on_demo_write() -> None:
    t = DemoSpinTracker(nudge_after=3, stop_after=5)
    for _ in range(4):
        t.note_tool_end("read")
    assert t.spin_count >= 3
    t.note_tool_end("lw_write", path_or_title=".kw/demo/index.html", ok=True)
    assert t.spin_count == 0
    assert not t.should_stop()


def test_spin_reset_on_gaps_shrink() -> None:
    t = DemoSpinTracker()
    t.note_gaps(["a", "b"])
    t.note_tool_end("read")
    t.note_tool_end("read")
    assert t.spin_count == 2
    t.note_gaps(["a"])
    assert t.spin_count == 0


def test_defaults_match_spec() -> None:
    assert SPIN_NUDGE_AFTER == 8
    assert SPIN_STOP_AFTER == 16


def test_synthesize_bash_denied_no_dump() -> None:
    text = synthesize_incomplete_reply(
        cancel_reason="demo_bash_denied",
        missing=[".kw/demo/css/style.css"],
        present=[".kw/demo/index.html"],
        can_continue=True,
    )
    assert "拦截" in text or "危险" in text
    assert "Objective" not in text
    assert "{" not in text or "demo" in text.lower()


def test_synthesize_spin_mentions_not_wall_clock() -> None:
    text = synthesize_incomplete_reply(
        cancel_reason="demo_spin",
        missing=[".kw/demo/js/app.js"],
        can_continue=True,
    )
    assert "空转" in text
    assert "超时" in text  # explains it is NOT fixed timeout
