"""Download Skill archives to an isolated temp dir (§13.3)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB

HttpFetch = Callable[[str, dict[str, str] | None], bytes]


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    dest: Path
    commit_or_version: str
    file_count: int
    total_bytes: int
    sha256: str
    source_url: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dest": str(self.dest),
            "commit_or_version": self.commit_or_version,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
            "source_url": self.source_url,
            "warnings": list(self.warnings),
        }


def _safe_members(members: list[tarfile.TarInfo], dest: Path) -> list[tarfile.TarInfo]:
    safe: list[tarfile.TarInfo] = []
    dest_res = dest.resolve()
    for m in members:
        if not m.isfile() and not m.isdir():
            continue
        name = m.name.replace("\\", "/")
        if name.startswith("/") or name.startswith("..") or "/../" in f"/{name}/":
            raise FetchError(f"path_traversal:{name}")
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest_res)):
            raise FetchError(f"path_traversal:{name}")
        safe.append(m)
    return safe


def extract_archive(
    data: bytes,
    dest: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[int, int]:
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    count = 0
    # Detect format
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                raise FetchError("too_many_files")
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    raise FetchError(f"path_traversal:{name}")
                if info.is_dir():
                    (dest / name).mkdir(parents=True, exist_ok=True)
                    continue
                total += info.file_size
                if total > max_bytes:
                    raise FetchError("archive_too_large")
                target = (dest / name).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise FetchError(f"path_traversal:{name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    out.write(src.read())
                count += 1
        return count, total

    # tar / tar.gz
    mode = "r:gz" if data[:2] == b"\x1f\x8b" else "r:*"
    with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
        members = [m for m in tf.getmembers() if m.isfile() or m.isdir()]
        if len(members) > max_files:
            raise FetchError("too_many_files")
        safe = _safe_members(members, dest)
        for m in safe:
            if m.isdir():
                (dest / m.name).mkdir(parents=True, exist_ok=True)
                continue
            total += int(m.size or 0)
            if total > max_bytes:
                raise FetchError("archive_too_large")
            tf.extract(m, path=dest, filter="data") if hasattr(tarfile, "data_filter") else tf.extract(m, path=dest)
            count += 1
    return count, total


def fetch_github_tarball(
    repo: str,
    commit: str,
    dest: Path,
    *,
    http_fetch: HttpFetch | None = None,
    token: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FetchResult:
    """Fetch ``https://api.github.com/repos/{repo}/tarball/{commit}`` — no git hooks."""
    if not commit or commit.lower() in ("latest", "main", "master", "head"):
        # Allow main only if explicit SHA-like; require pinned SHA for install path
        if len(commit) < 7 or commit.lower() in ("latest", "head"):
            raise FetchError("unpinned_ref:require_commit_sha")
    url = f"https://api.github.com/repos/{repo}/tarball/{commit}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if http_fetch is None:
        raise FetchError("no_http_fetch")
    data = http_fetch(url, headers)
    if len(data) > max_bytes:
        raise FetchError("download_too_large")
    sha = hashlib.sha256(data).hexdigest()
    count, total = extract_archive(data, dest, max_files=max_files, max_bytes=max_bytes)
    return FetchResult(
        dest=dest,
        commit_or_version=commit,
        file_count=count,
        total_bytes=total,
        sha256=sha,
        source_url=url,
    )


def materialize_tree(files: dict[str, str | bytes], dest: Path) -> FetchResult:
    """Write an in-memory file tree (for tests / local packs)."""
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    h = hashlib.sha256()
    for rel, content in sorted(files.items()):
        rel_n = rel.replace("\\", "/")
        if rel_n.startswith("/") or ".." in rel_n.split("/"):
            raise FetchError(f"path_traversal:{rel_n}")
        path = dest / rel_n
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        path.write_bytes(data)
        total += len(data)
        h.update(rel_n.encode())
        h.update(data)
    return FetchResult(
        dest=dest,
        commit_or_version="local",
        file_count=len(files),
        total_bytes=total,
        sha256=h.hexdigest(),
    )
