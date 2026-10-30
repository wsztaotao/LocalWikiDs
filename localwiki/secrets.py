"""Secret storage: Windows Credential Manager / DPAPI file fallback (§6.1, §14.2).

Secrets NEVER appear in workspace plaintext, logs, or backups.
Config files may only reference ``secret:<name>``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SERVICE_NAME = "LocalWiki"
SECRET_REF_RE = re.compile(r"^secret:([A-Za-z0-9_.\-]+)$")


class SecretError(RuntimeError):
    pass


class SecretBackend(Protocol):
    def set(self, name: str, value: str) -> None: ...
    def get(self, name: str) -> str | None: ...
    def delete(self, name: str) -> bool: ...
    def list_names(self) -> list[str]: ...


@dataclass
class MemoryBackend:
    """In-memory backend for unit tests only."""

    _data: dict[str, str]

    def __init__(self) -> None:
        self._data = {}

    def set(self, name: str, value: str) -> None:
        self._data[name] = value

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def delete(self, name: str) -> bool:
        return self._data.pop(name, None) is not None

    def list_names(self) -> list[str]:
        return sorted(self._data)


class KeyringBackend:
    def __init__(self, service: str = SERVICE_NAME) -> None:
        try:
            import keyring  # type: ignore
        except ImportError as exc:
            raise SecretError("E_DEP_MISSING: keyring") from exc
        self._keyring = keyring
        self._service = service
        self._index_user = "__localwiki_secret_index__"

    def _load_index(self) -> list[str]:
        raw = self._keyring.get_password(self._service, self._index_user)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return list(data) if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _save_index(self, names: list[str]) -> None:
        self._keyring.set_password(
            self._service, self._index_user, json.dumps(sorted(set(names)))
        )

    def set(self, name: str, value: str) -> None:
        self._keyring.set_password(self._service, name, value)
        names = self._load_index()
        if name not in names:
            names.append(name)
            self._save_index(names)

    def get(self, name: str) -> str | None:
        return self._keyring.get_password(self._service, name)

    def delete(self, name: str) -> bool:
        try:
            self._keyring.delete_password(self._service, name)
        except Exception:
            return False
        names = [n for n in self._load_index() if n != name]
        self._save_index(names)
        return True

    def list_names(self) -> list[str]:
        return self._load_index()


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        # Non-Windows test fallback: obfuscation only (NOT production-grade).
        return b"XOR1" + bytes(b ^ 0x5A for b in data)
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    blob_in = DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise SecretError("DPAPI CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        if data.startswith(b"XOR1"):
            return bytes(b ^ 0x5A for b in data[4:])
        raise SecretError("invalid non-windows secret blob")
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    blob_in = DATA_BLOB(
        len(data),
        ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)),
    )
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise SecretError("DPAPI CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


class DpapiFileBackend:
    """Encrypted secrets file at ``<workspace>/.kw/secrets.bin`` (DPAPI on Windows)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        raw = self.path.read_bytes()
        plain = _dpapi_unprotect(raw)
        data = json.loads(plain.decode("utf-8"))
        return dict(data) if isinstance(data, dict) else {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        blob = _dpapi_protect(payload)
        self.path.write_bytes(blob)
        # Best-effort: restrict ACL not portable; avoid world-readable on Unix.
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def get(self, name: str) -> str | None:
        return self._load().get(name)

    def delete(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True

    def list_names(self) -> list[str]:
        return sorted(self._load())


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Redact known secret values and common key patterns from log text."""
    out = text
    for s in secrets or []:
        if s and s in out:
            out = out.replace(s, "***REDACTED***")
    # Generic API key-ish tokens
    out = re.sub(
        r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*([^\s\"']+)",
        r"\1=***REDACTED***",
        out,
    )
    return out


@dataclass
class SecretStore:
    backend: SecretBackend

    def set(self, name: str, value: str) -> str:
        """Store secret; returns reference ``secret:<name>`` for configs."""
        _validate_name(name)
        if not value:
            raise SecretError("empty secret value")
        self.backend.set(name, value)
        return f"secret:{name}"

    def get(self, name: str) -> str | None:
        _validate_name(name)
        return self.backend.get(name)

    def resolve(self, ref_or_value: str) -> str:
        """Resolve ``secret:name``; pass through non-refs unchanged (discouraged)."""
        m = SECRET_REF_RE.match(ref_or_value.strip())
        if not m:
            return ref_or_value
        val = self.get(m.group(1))
        if val is None:
            raise SecretError(f"secret not found: {m.group(1)}")
        return val

    def delete(self, name: str) -> bool:
        _validate_name(name)
        return self.backend.delete(name)

    def list_names(self) -> list[str]:
        return self.backend.list_names()


def _validate_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
        raise SecretError(f"invalid secret name: {name!r}")


def open_secret_store(
    workspace: Path | None = None,
    *,
    prefer_keyring: bool = True,
    force_memory: bool = False,
) -> SecretStore:
    if force_memory:
        return SecretStore(MemoryBackend())
    # Prefer workspace DPAPI file when present. Anaconda/other envs may have
    # empty keyring; preferring keyring then yields "secret not found" while
    # secrets.bin still holds the real keys (OpenCode inject failure).
    if workspace is not None:
        path = Path(workspace) / ".kw" / "secrets.bin"
        if path.is_file():
            return SecretStore(DpapiFileBackend(path))
    if prefer_keyring:
        try:
            return SecretStore(KeyringBackend())
        except SecretError:
            pass
        except Exception:
            pass
    if workspace is None:
        raise SecretError("workspace required for DPAPI file backend")
    path = Path(workspace) / ".kw" / "secrets.bin"
    return SecretStore(DpapiFileBackend(path))


def scan_paths_for_plaintext_secrets(
    roots: list[Path],
    secret_values: list[str],
    *,
    ignore_names: frozenset[str] | None = None,
) -> list[str]:
    """Return relative hits where a secret value appears in plaintext files.

    Skips ``.kw/secrets.bin`` (encrypted blob may coincidentally contain bytes).
    """
    ignore = ignore_names or frozenset({"secrets.bin"})
    hits: list[str] = []
    values = [v for v in secret_values if v]
    if not values:
        return hits
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in ignore:
                continue
            # Skip likely binary
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for val in values:
                if val in text:
                    try:
                        rel = str(path.relative_to(root)).replace("\\", "/")
                    except ValueError:
                        rel = str(path)
                    hits.append(f"{rel}:contains_secret")
                    break
    return hits
