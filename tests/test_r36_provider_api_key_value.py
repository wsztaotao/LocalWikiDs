"""R3.6 patch: api_key_value → SecretStore → secret: ref."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from localwiki.personal_core import get_core_model_settings, save_core_model_settings  # noqa: E402
from localwiki.secrets import open_secret_store  # noqa: E402
from localwiki.workspace import ensure_workspace  # noqa: E402


def test_api_key_value_stored_as_secret(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ensure_workspace(ws)
    out = save_core_model_settings(
        ws,
        {
            "providers": {
                "deepseek": {
                    "kind": "openai_compatible",
                    "location": "online",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key_value": "sk-test-deepseek-key",
                    "secret_name": "deepseek",
                }
            }
        },
    )
    assert out["providers"]["deepseek"]["api_key"] == "secret:deepseek"
    assert out["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
    store = open_secret_store(ws)
    assert store.get("deepseek") == "sk-test-deepseek-key"
    view = get_core_model_settings(ws)
    assert view["providers"]["deepseek"]["api_key"].startswith("secret:")
