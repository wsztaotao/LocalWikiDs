"""Tests for localwiki.secrets (LW-12)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.secrets import (  # noqa: E402
    DpapiFileBackend,
    SecretStore,
    open_secret_store,
    redact,
    scan_paths_for_plaintext_secrets,
)


def test_memory_set_get_delete() -> None:
    store = open_secret_store(force_memory=True)
    ref = store.set("openai_main", "sk-test-secret-value-12345")
    assert ref == "secret:openai_main"
    assert store.get("openai_main") == "sk-test-secret-value-12345"
    assert store.resolve("secret:openai_main") == "sk-test-secret-value-12345"
    assert "openai_main" in store.list_names()
    assert store.delete("openai_main") is True
    assert store.get("openai_main") is None


def test_dpapi_file_backend_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / ".kw" / "secrets.bin"
    backend = DpapiFileBackend(path)
    store = SecretStore(backend)
    secret = "composio-key-SHOULD-NOT-LEAK"
    store.set("composio", secret)
    assert path.is_file()
    # File must not contain plaintext secret
    raw = path.read_bytes()
    assert secret.encode("utf-8") not in raw
    assert store.get("composio") == secret


def test_secret_not_in_workspace_files_logs_backups(tmp_path: Path) -> None:
    ws = tmp_path / "LocalWikiData"
    (ws / ".kw").mkdir(parents=True)
    (ws / "wiki").mkdir()
    store = SecretStore(DpapiFileBackend(ws / ".kw" / "secrets.bin"))
    secret = "ghp_LEAKDETECT_9f8e7d6c5b4a3210"
    store.set("github_token", secret)

    # Simulate config with reference only
    routes = {
        "version": 2,
        "providers": {
            "openai_main": {
                "kind": "openai_compatible",
                "api_key": "secret:github_token",
                "location": "online",
            }
        },
        "routes": {},
    }
    (ws / ".kw" / "model-routes.json").write_text(
        json.dumps(routes, indent=2), encoding="utf-8"
    )
    # Fake log and backup dirs
    log = ws / ".kw" / "runtime" / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(redact(f"connecting with api_key={secret}\n", [secret]), encoding="utf-8")
    backup = ws / "backups" / "backup.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text("backup without secrets\n", encoding="utf-8")

    hits = scan_paths_for_plaintext_secrets(
        [ws],
        [secret],
    )
    assert hits == [], hits


def test_scan_detects_accidental_plaintext(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = "sk-SHOULD-BE-FOUND"
    (ws / "leaked.txt").write_text(f"key={secret}\n", encoding="utf-8")
    hits = scan_paths_for_plaintext_secrets([ws], [secret])
    assert hits


def test_redact() -> None:
    s = "Authorization: Bearer abc123 and api_key=xyz"
    out = redact(s, ["abc123"])
    assert "abc123" not in out
    assert "***REDACTED***" in out


def test_open_secret_store_prefers_secrets_bin_over_keyring(
    tmp_path: Path, monkeypatch
) -> None:
    """Anaconda+keyring must not mask workspace secrets.bin (OC inject bug)."""
    ws = tmp_path / "ws"
    (ws / ".kw").mkdir(parents=True)
    path = ws / ".kw" / "secrets.bin"
    SecretStore(DpapiFileBackend(path)).set("qwen", "sk-from-dpapi-file")

    class _EmptyKeyring:
        def get(self, name: str) -> str | None:
            return None

        def set(self, name: str, value: str) -> None:
            raise AssertionError("should not use keyring when secrets.bin exists")

        def delete(self, name: str) -> bool:
            return False

        def list_names(self) -> list[str]:
            return []

    monkeypatch.setattr(
        "localwiki.secrets.KeyringBackend",
        lambda *a, **k: _EmptyKeyring(),
    )
    store = open_secret_store(ws, prefer_keyring=True)
    assert store.resolve("secret:qwen") == "sk-from-dpapi-file"
