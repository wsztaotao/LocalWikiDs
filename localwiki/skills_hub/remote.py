"""Fetch Skill/Harness trees from GitHub URL or repo (R3.6)."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from localwiki.skills_hub.fetch import FetchError

HttpBytes = Callable[[str, dict[str, str] | None], bytes]

_GITHUB_TREE = re.compile(
    r"^https?://github\.com/(?P<repo>[^/]+/[^/]+)/(?:tree|blob)/(?P<ref>[^/]+)(?:/(?P<path>.*))?$"
)


def parse_github_source_url(url: str) -> dict[str, str]:
    """Parse github.com tree/blob URL → repo, ref, path."""
    m = _GITHUB_TREE.match((url or "").strip())
    if not m:
        raise FetchError(f"unsupported_source_url:{url}")
    path = (m.group("path") or "").rstrip("/")
    return {"repo": m.group("repo"), "ref": m.group("ref"), "path": path}


def _zip_root_prefix(names: list[str]) -> str:
    if not names:
        return ""
    first = names[0].replace("\\", "/")
    return first.split("/")[0] + "/"


def _skill_md_parents(names: list[str], root_prefix: str) -> list[str]:
    """Relative parent dirs of SKILL.md under the zip root (no trailing slash)."""
    parents: list[str] = []
    for name in names:
        n = name.replace("\\", "/")
        if n.endswith("/") or not n.startswith(root_prefix):
            continue
        rel = n[len(root_prefix) :]
        if rel.lower().endswith("skill.md") and Path(rel).name.lower() == "skill.md":
            parent = str(Path(rel).parent).replace("\\", "/")
            if parent in (".", ""):
                parents.append("")
            else:
                parents.append(parent)
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in parents:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _zip_to_files(data: bytes, *, subpath: str = "") -> dict[str, bytes]:
    sub = subpath.strip("/").replace("\\", "/")
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        root_prefix = _zip_root_prefix(names)
        prefix = root_prefix + (sub + "/" if sub else "")
        for name in names:
            n = name.replace("\\", "/")
            if n.endswith("/"):
                continue
            if not n.startswith(prefix):
                continue
            rel = n[len(prefix) :]
            if not rel or ".." in rel.split("/"):
                continue
            out[rel] = zf.read(name)
    if not out:
        raise FetchError("empty_subtree_or_path_miss")
    return out


def fetch_github_as_files(
    *,
    repo: str,
    ref: str = "main",
    path: str = "",
    http_fetch: HttpBytes | None = None,
    token: str | None = None,
) -> dict[str, bytes]:
    """Download GitHub archive zip and return relative file map under ``path``."""
    if not repo or "/" not in repo:
        raise FetchError("bad_repo")
    if http_fetch is None:
        raise FetchError("no_http_fetch")
    # codeload supports branch names and SHAs
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/{ref}"
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        data = http_fetch(url, headers)
    except Exception:
        # fallback: treat ref as tag/sha
        url2 = f"https://codeload.github.com/{repo}/zip/{ref}"
        data = http_fetch(url2, headers)
    if not data or data[:2] != b"PK":
        raise FetchError("not_a_zip_archive")

    sub = (path or "").strip("/").replace("\\", "/")
    try:
        return _zip_to_files(data, subpath=sub)
    except FetchError as exc:
        if "empty_subtree_or_path_miss" not in str(exc):
            raise
        # Auto-pick unique SKILL.md parent when path empty or path miss
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            root_prefix = _zip_root_prefix(names)
            parents = _skill_md_parents(names, root_prefix)
        if len(parents) == 1:
            return _zip_to_files(data, subpath=parents[0])
        if not parents:
            raise FetchError("empty_subtree_or_path_miss") from exc
        preview = ", ".join(p or "(repo-root)" for p in parents[:5])
        raise FetchError(
            f"ambiguous_skill_paths:{len(parents)} candidates e.g. {preview}"
        ) from exc


def default_http_fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def resolve_install_files(
    *,
    files: dict[str, str | bytes] | None = None,
    source_url: str | None = None,
    repo: str | None = None,
    path: str | None = None,
    ref: str | None = None,
    http_fetch: HttpBytes | None = None,
    token: str | None = None,
) -> tuple[dict[str, str | bytes], dict[str, Any]]:
    """Resolve files map + source metadata for install."""
    if files:
        return files, {"type": "files", "source_url": source_url, "repo": repo}
    meta: dict[str, Any] = {"type": "github"}
    if source_url:
        parsed = urlparse(source_url)
        if "github.com" in (parsed.netloc or ""):
            g = parse_github_source_url(source_url)
            repo = g["repo"]
            ref = ref or g["ref"]
            path = path if path is not None else g["path"]
            meta["source_url"] = source_url
        else:
            raise FetchError("only_github_source_url_supported")
    if not repo:
        raise FetchError("files_or_repo_or_source_url_required")
    meta["repo"] = repo
    meta["ref"] = ref or "main"
    meta["path"] = path or ""
    fetch = http_fetch or default_http_fetch
    blob = fetch_github_as_files(
        repo=repo,
        ref=meta["ref"],
        path=str(meta["path"]),
        http_fetch=fetch,
        token=token,
    )
    return blob, meta
