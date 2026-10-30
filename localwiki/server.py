"""Minimal HTTP server: 127.0.0.1 + Token + R1 API (spec §5)."""

from __future__ import annotations

import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from localwiki.core import CommitError, CommitService
from localwiki.diagnostics import doctor
from localwiki.single_instance import WorkspaceLock
from localwiki.workspace import (
    ensure_workspace,
    load_config,
    read_head,
    save_config,
    web_static_dir,
)

VERSION = "0.12.0-r35-pc"


def _err(code: str, message: str, *, recoverable: bool = True) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "recoverable": recoverable,
        },
    }


def _ok(data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


class LocalWikiServer:
    def __init__(
        self,
        workspace: Path | str,
        *,
        host: str = "127.0.0.1",
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        self.root = Path(workspace).resolve()
        self.paths = ensure_workspace(self.root)
        self.cfg = load_config(self.paths["config"])
        self.host = host
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("server must bind to loopback only")
        self.port = int(port if port is not None else self.cfg.get("port", 8765))
        self.token = token or str(self.cfg.get("api_token") or "")
        if not self.token:
            raise RuntimeError("api_token missing; run ensure_workspace first")
        self.commits = CommitService(self.root)
        self.lock = WorkspaceLock(self.paths["lock"])
        self._httpd: ThreadingHTTPServer | None = None
        self.recovery_notes = self.commits.recover_on_startup()

    def acquire_runtime_lock(self) -> bool:
        return self.lock.acquire(blocking=False)

    def release_runtime_lock(self) -> None:
        self.lock.release()

    def make_handler(self) -> type[BaseHTTPRequestHandler]:
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                return  # quiet tests

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise CommitError("E_INVALID_INPUT", f"bad json: {exc}") from exc
                if not isinstance(data, dict):
                    raise CommitError("E_INVALID_INPUT", "json object required")
                return data

            def _check_auth(self) -> bool:
                auth = self.headers.get("Authorization") or ""
                token = self.headers.get("X-LocalWiki-Token") or ""
                if auth.lower().startswith("bearer "):
                    token = auth[7:].strip()
                return token == server_ref.token and bool(token)

            def _send(
                self,
                status: int,
                payload: dict[str, Any] | bytes,
                *,
                content_type: str = "application/json",
                content_disposition: str | None = None,
            ) -> None:
                if isinstance(payload, dict):
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                else:
                    body = payload
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if content_disposition:
                    self.send_header("Content-Disposition", content_disposition)
                self.end_headers()
                self.wfile.write(body)

            def _require_auth(self) -> bool:
                if not self._check_auth():
                    self._send(401, _err("E_PERMISSION", "invalid or missing token"))
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                qs = parse_qs(parsed.query)

                # Static + index (no auth for shell; API still needs token)
                if path in ("/", "/index.html"):
                    self._serve_static("index.html")
                    return
                if path.startswith("/web/"):
                    self._serve_static(path[len("/web/") :])
                    return
                # R3.23: workspace demo preview (no auth; sandboxed to .kw/demo)
                if path == "/demo" or path.startswith("/demo/"):
                    self._serve_demo(path)
                    return
                # Also allow /style.css /app.js at root for simplicity
                if path in ("/app.js", "/style.css") or path.endswith(
                    (".js", ".css", ".svg", ".png", ".ico")
                ):
                    self._serve_static(path.lstrip("/"))
                    return

                if not path.startswith("/api/"):
                    self._send(404, _err("E_NOT_FOUND", path))
                    return
                # R3.5: loopback-only token bootstrap (no Bearer required)
                if path == "/api/local-token":
                    self._api_local_token()
                    return
                if not self._require_auth():
                    return
                try:
                    self._api_get(path, qs)
                except CommitError as exc:
                    status = 409 if exc.code == "E_CONFLICT" else 400
                    if exc.code == "E_NOT_FOUND":
                        status = 404
                    if exc.code == "E_PERMISSION":
                        status = 403
                    self._send(status, _err(exc.code, exc.message))
                except Exception as exc:  # noqa: BLE001
                    self._send(
                        500,
                        _err("E_FAILED", str(exc), recoverable=False),
                    )

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if not path.startswith("/api/"):
                    self._send(404, _err("E_NOT_FOUND", path))
                    return
                if not self._require_auth():
                    return
                try:
                    ctype = self.headers.get("Content-Type") or ""
                    if path == "/api/import" and "multipart/form-data" in ctype:
                        self._api_import_multipart(ctype)
                        return
                    body = self._read_json()
                    self._api_post(path, body)
                except CommitError as exc:
                    status = 409 if exc.code == "E_CONFLICT" else 400
                    if exc.code == "E_NOT_FOUND":
                        status = 404
                    if exc.code == "E_PERMISSION":
                        status = 403
                    self._send(status, _err(exc.code, exc.message))
                except Exception as exc:  # noqa: BLE001
                    from localwiki.extractors import ExtractError
                    from localwiki.recovery import RecoveryError

                    if isinstance(exc, (ExtractError, RecoveryError)):
                        self._send(400, _err(exc.code, exc.message))
                        return
                    self._send(
                        500,
                        _err(
                            "E_FAILED",
                            f"{exc}\n{traceback.format_exc()}",
                            recoverable=False,
                        ),
                    )

            def _api_import_multipart(self, ctype: str) -> None:
                import re

                from localwiki.sources import import_bytes

                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b""
                m = re.search(r"boundary=([^;]+)", ctype, re.I)
                if not m:
                    raise CommitError("E_INVALID_INPUT", "multipart boundary missing")
                boundary = m.group(1).strip().strip('"').encode("ascii")
                parts = raw.split(b"--" + boundary)
                filename = "upload.bin"
                file_data: bytes | None = None
                for part in parts:
                    if b"Content-Disposition" not in part:
                        continue
                    header, _, body = part.partition(b"\r\n\r\n")
                    body = body.rstrip(b"\r\n")
                    if body.endswith(b"--"):
                        body = body[:-2].rstrip(b"\r\n")
                    hm = re.search(
                        br'filename="([^"]+)"',
                        header,
                        re.I,
                    )
                    if hm:
                        filename = hm.group(1).decode("utf-8", errors="replace")
                        file_data = body
                        break
                if file_data is None:
                    raise CommitError("E_INVALID_INPUT", "file part required")
                meta = import_bytes(server_ref.root, file_data, filename=filename)
                self._send(200, _ok(meta))

            def _serve_static(self, rel: str) -> None:
                web = web_static_dir()
                target = (web / rel).resolve()
                try:
                    target.relative_to(web.resolve())
                except ValueError:
                    self._send(404, _err("E_NOT_FOUND", rel))
                    return
                if not target.is_file():
                    self._send(404, _err("E_NOT_FOUND", rel))
                    return
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self._send(200, target.read_bytes(), content_type=ctype)

            def _serve_demo(self, path: str) -> None:
                from localwiki.runtime.demo_static import guess_demo_ctype, resolve_demo_file

                fp = resolve_demo_file(server_ref.root, path)
                if fp is None:
                    self._send(404, _err("E_NOT_FOUND", path))
                    return
                self._send(200, fp.read_bytes(), content_type=guess_demo_ctype(fp))

            def _client_is_loopback(self) -> bool:
                host = (self.client_address[0] if self.client_address else "") or ""
                if host in ("127.0.0.1", "::1", "localhost"):
                    return True
                # IPv6-mapped IPv4
                return host.endswith("127.0.0.1")

            def _api_local_token(self) -> None:
                if not self._client_is_loopback():
                    self._send(
                        403,
                        _err("E_PERMISSION", "local-token only allowed from 127.0.0.1"),
                    )
                    return
                self._send(
                    200,
                    _ok(
                        {
                            "token": server_ref.token,
                            "port": server_ref.port,
                            "workspace": str(server_ref.root),
                        }
                    ),
                )

            def _api_get(self, path: str, qs: dict[str, list[str]]) -> None:
                svc = server_ref.commits
                if path == "/api/status":
                    d = doctor(server_ref.root, ensure=False)
                    lock_info = server_ref.lock.info()
                    from localwiki.personal_core import loop_enabled
                    from localwiki.runtime.opencode_bridge import opencode_status

                    oc = opencode_status(server_ref.root)
                    self._send(
                        200,
                        _ok(
                            {
                                "version": VERSION,
                                "workspace": str(server_ref.root),
                                "snapshot_id": read_head(server_ref.root),
                                "doctor_ok": d.get("ok"),
                                "index_stale": d.get("index_stale"),
                                "locked": bool(lock_info and lock_info.get("alive")),
                                "lock": lock_info,
                                "recovery_notes": server_ref.recovery_notes,
                                "port": server_ref.port,
                                "token_present": bool(server_ref.token),
                                "token": server_ref.token,
                                "opencode": oc,
                                "local_agent_loop_enabled": loop_enabled(server_ref.root),
                                "build_hint": (
                                    None
                                    if oc.get("ready")
                                    else "Run scripts/build_opencode.ps1 (requires Bun)"
                                ),
                                "integrations": (
                                    __import__(
                                        "localwiki.integrations_live",
                                        fromlist=["live_tool_status"],
                                    ).live_tool_status(server_ref.root)
                                ),
                            }
                        ),
                    )
                    return
                if path == "/api/settings":
                    cfg = load_config(server_ref.paths["config"])
                    from localwiki.personal_core import list_provider_presets, loop_enabled
                    from localwiki.skills_hub.github_auth import github_token_configured

                    autonomy = cfg.get("autonomy") if isinstance(cfg.get("autonomy"), dict) else {}
                    self._send(
                        200,
                        _ok(
                            {
                                "workspace": str(server_ref.root),
                                "workspace_uid": cfg.get("workspace_uid"),
                                "port": cfg.get("port", 8765),
                                "token_saved": bool(cfg.get("api_token")),
                                "local_agent_loop_enabled": loop_enabled(server_ref.root),
                                "autonomy": autonomy,
                                "provider_presets": list_provider_presets().get("presets"),
                                "github_token_configured": github_token_configured(
                                    server_ref.root
                                ),
                            }
                        ),
                    )
                    return
                if path == "/api/settings/models/ollama-tags":
                    from localwiki.personal_core import list_ollama_tags

                    base = (qs.get("base_url") or [None])[0]
                    self._send(
                        200,
                        _ok(
                            list_ollama_tags(
                                server_ref.root,
                                base_url=str(base) if base else None,
                            )
                        ),
                    )
                    return
                if path == "/api/settings/models/provider-models":
                    from localwiki.personal_core import list_provider_models

                    provider = (qs.get("provider") or [""])[0]
                    self._send(
                        200,
                        _ok(
                            list_provider_models(
                                server_ref.root,
                                str(provider or ""),
                            )
                        ),
                    )
                    return
                if path == "/api/settings/models/presets":
                    from localwiki.personal_core import list_provider_presets

                    self._send(200, _ok(list_provider_presets()))
                    return
                if path == "/api/proposals":
                    self._send(200, _ok(svc.list_changesets()))
                    return
                if path.startswith("/api/proposals/"):
                    uid = path[len("/api/proposals/") :]
                    cs = svc.get_changeset(uid)
                    self._send(200, _ok(cs.to_dict()))
                    return
                if path == "/api/pages":
                    self._send(200, _ok(svc.list_pages()))
                    return
                if path == "/api/pages/content":
                    page_path = (qs.get("path") or [""])[0]
                    if not page_path:
                        raise CommitError("E_INVALID_INPUT", "path required")
                    self._send(200, _ok(svc.read_page(page_path)))
                    return
                if path == "/api/wiki/graph":
                    from localwiki.wiki_graph import build_wiki_graph

                    def _flag(name: str, default: bool = True) -> bool:
                        raw = (qs.get(name) or [None])[0]
                        if raw is None:
                            return default
                        return str(raw).strip().lower() not in ("0", "false", "no", "off")

                    view = (qs.get("view") or [None])[0]
                    cats = (qs.get("cats") or [None])[0]
                    q = (qs.get("q") or [None])[0]
                    self._send(
                        200,
                        _ok(
                            build_wiki_graph(
                                server_ref.root,
                                view=view,
                                cats=cats,
                                weak=_flag("weak", True),
                                hide_isolates=_flag("hide_isolates", False),
                                cluster=_flag("cluster", True),
                                q=q,
                            )
                        ),
                    )
                    return
                if path == "/api/wiki/dedup-scan":
                    # GET returns last-run hint only; POST performs scan
                    self._send(
                        200,
                        _ok({"hint": "POST /api/wiki/dedup-scan with {apply?: bool}"}),
                    )
                    return
                if path == "/api/sessions":
                    from localwiki.sessions import SessionStore

                    self._send(200, _ok(SessionStore(server_ref.root).list_sessions()))
                    return
                if path == "/api/sessions/queue":
                    from localwiki.runtime.session_runner import get_runner

                    self._send(200, _ok(get_runner(server_ref.root).snapshot()))
                    return
                if path == "/api/sessions/storage":
                    from localwiki.sessions import SessionStore

                    self._send(200, _ok(SessionStore(server_ref.root).storage_stats()))
                    return
                # R3.10 SSE: /api/sessions/jobs/{job_id}/events
                if path.startswith("/api/sessions/jobs/") and path.endswith("/events"):
                    from localwiki.runtime.session_runner import format_sse, get_runner

                    mid = path[len("/api/sessions/jobs/") : -len("/events")].strip("/")
                    if not mid:
                        raise CommitError("E_INVALID_INPUT", "job id required")
                    runner = get_runner(server_ref.root)
                    job = runner.get_job(mid)
                    if job is None:
                        raise CommitError("E_NOT_FOUND", mid)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    idx = 0
                    # Replay existing events then wait for more until done
                    idle_rounds = 0
                    while True:
                        batch = job.wait_events(idx, timeout=1.0)
                        if batch:
                            idle_rounds = 0
                            for ev in batch:
                                self.wfile.write(format_sse(ev))
                                self.wfile.flush()
                                idx += 1
                                if ev.get("type") == "done":
                                    return
                        else:
                            idle_rounds += 1
                            # heartbeat
                            self.wfile.write(format_sse({"type": "ping"}))
                            self.wfile.flush()
                            if job.status in ("done", "error", "cancelled") and idx >= len(
                                job.stream_events
                            ):
                                # ensure done was sent
                                if not any(
                                    e.get("type") == "done" for e in job.stream_events
                                ):
                                    self.wfile.write(
                                        format_sse(
                                            {
                                                "type": "done",
                                                "ok": job.status == "done",
                                                "cancelled": job.status == "cancelled",
                                                "content": job.stream_text,
                                                "error": job.error,
                                                "wiki_hit": job.wiki_hit,
                                                "wiki_paths": job.wiki_paths,
                                            }
                                        )
                                    )
                                    self.wfile.flush()
                                return
                            if idle_rounds > 300:
                                return
                    return
                if path.startswith("/api/sessions/"):
                    from localwiki.sessions import SessionStore

                    sid = path[len("/api/sessions/") :].strip("/")
                    if not sid or sid in ("queue", "storage", "clear", "jobs"):
                        raise CommitError("E_INVALID_INPUT", "session id required")
                    try:
                        self._send(200, _ok(SessionStore(server_ref.root).get(sid)))
                    except FileNotFoundError:
                        raise CommitError("E_NOT_FOUND", sid) from None
                    return
                if path == "/api/sources":
                    from localwiki.sources import list_sources

                    self._send(200, _ok(list_sources(server_ref.root)))
                    return
                if path == "/api/personal/context":
                    from localwiki.personal_core import build_agent_context

                    self._send(
                        200,
                        _ok(build_agent_context(server_ref.root, surface="agent")),
                    )
                    return
                if path == "/api/personal/routes":
                    from localwiki.personal_core import list_core_routes

                    self._send(200, _ok({"routes": list_core_routes()}))
                    return
                if path == "/api/settings/models":
                    from localwiki.personal_core import get_core_model_settings

                    self._send(200, _ok(get_core_model_settings(server_ref.root)))
                    return
                if path == "/api/settings/workbench-presets":
                    from localwiki.workbench_presets import load_presets

                    self._send(200, _ok(load_presets(server_ref.root)))
                    return
                if path == "/api/opencode/status":
                    from localwiki.runtime.opencode_bridge import opencode_status

                    st = opencode_status(server_ref.root)
                    skills = server_ref.root / ".kw" / "skills"
                    st["skills_path"] = str(skills) if skills.is_dir() else None
                    st["skills_aligned"] = bool(skills.is_dir() and st.get("ready"))
                    self._send(200, _ok(st))
                    return
                if path == "/api/evolution/status":
                    from localwiki.evolution.gateway import status as evo_status

                    self._send(200, _ok(evo_status(server_ref.root)))
                    return
                if path == "/api/evolution/learnings":
                    from localwiki.evolution.gateway import list_learnings

                    self._send(200, _ok(list_learnings(server_ref.root)))
                    return
                if path == "/api/evolution/proposals":
                    from localwiki.evolution.gateway import list_proposals

                    self._send(200, _ok(list_proposals(server_ref.root)))
                    return
                if path == "/api/skills":
                    from localwiki.skills_hub.harness import list_installed

                    self._send(200, _ok(list_installed(server_ref.root)))
                    return
                if path == "/api/exports":
                    from localwiki.exports import list_exports

                    self._send(200, _ok({"items": list_exports(server_ref.root)}))
                    return
                if path.startswith("/api/exports/"):
                    from localwiki.exports import resolve_export_existing

                    name = path[len("/api/exports/") :].strip("/")
                    if not name or "/" in name:
                        raise CommitError("E_INVALID_INPUT", "export name required")
                    fp = resolve_export_existing(server_ref.root, name)
                    if fp is None:
                        raise CommitError("E_NOT_FOUND", name)
                    ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
                    if fp.name.lower().endswith(".mp3"):
                        ctype = "audio/mpeg"
                    elif fp.name.lower().endswith(".wav"):
                        ctype = "audio/wav"
                    self._send(
                        200,
                        fp.read_bytes(),
                        content_type=ctype,
                        content_disposition=f'attachment; filename="{fp.name}"',
                    )
                    return
                self._send(501, _err("E_DEP_MISSING", f"not in Personal Core: {path}"))

            def _api_post(self, path: str, body: dict[str, Any]) -> None:
                svc = server_ref.commits
                if path == "/api/settings":
                    from localwiki.personal_core import loop_enabled, patch_autonomy_settings

                    cfg = load_config(server_ref.paths["config"])
                    if "port" in body:
                        cfg["port"] = int(body["port"])
                    if body.get("api_token"):
                        cfg["api_token"] = str(body["api_token"])
                        server_ref.token = cfg["api_token"]
                    if "port" in body or body.get("api_token"):
                        save_config(server_ref.paths["config"], cfg)
                    need_autonomy = any(
                        k in body
                        for k in (
                            "local_agent_loop_enabled",
                            "nightly_local_hour",
                            "pause_evolution",
                        )
                    )
                    if need_autonomy:
                        try:
                            patch_autonomy_settings(
                                server_ref.root,
                                nightly_local_hour=(
                                    int(body["nightly_local_hour"])
                                    if "nightly_local_hour" in body
                                    else None
                                ),
                                pause_evolution=(
                                    bool(body["pause_evolution"])
                                    if "pause_evolution" in body
                                    else None
                                ),
                                local_agent_loop_enabled=(
                                    bool(body["local_agent_loop_enabled"])
                                    if "local_agent_loop_enabled" in body
                                    else None
                                ),
                            )
                        except ValueError as exc:
                            raise CommitError("E_INVALID_INPUT", str(exc)) from exc
                    server_ref.cfg = load_config(server_ref.paths["config"])
                    autonomy = (
                        server_ref.cfg.get("autonomy")
                        if isinstance(server_ref.cfg.get("autonomy"), dict)
                        else {}
                    )
                    self._send(
                        200,
                        _ok(
                            {
                                "workspace_uid": server_ref.cfg.get("workspace_uid"),
                                "port": server_ref.cfg.get("port"),
                                "token_saved": bool(server_ref.cfg.get("api_token")),
                                "local_agent_loop_enabled": loop_enabled(server_ref.root),
                                "autonomy": autonomy,
                            }
                        ),
                    )
                    return
                if path == "/api/wiki/dedup-scan":
                    from localwiki.wiki_merge import scan_wiki_duplicates

                    apply = body.get("apply")
                    if apply is None:
                        apply = True
                    out = scan_wiki_duplicates(
                        server_ref.root, apply_high_confidence=bool(apply)
                    )
                    self._send(200, _ok(out))
                    return
                if path == "/api/propose":
                    cs = svc.create_changeset(
                        purpose=str(body.get("purpose") or ""),
                        ops=body.get("ops") or [],
                        state=str(body.get("state") or "ready"),
                    )
                    self._send(200, _ok(cs.to_dict()))
                    return
                if path == "/api/review":
                    uid = str(body.get("uid") or "")
                    action = str(body.get("action") or "")
                    if not uid or action not in ("accept", "reject"):
                        raise CommitError(
                            "E_INVALID_INPUT", "uid and action=accept|reject required"
                        )
                    if action == "accept":
                        cs = svc.accept(uid)
                    else:
                        cs = svc.reject(uid)
                    self._send(200, _ok(cs.to_dict()))
                    return
                if path == "/api/review/batch":
                    action = str(body.get("action") or "")
                    uids = body.get("uids") or []
                    if action not in ("accept", "reject") or not isinstance(uids, list):
                        raise CommitError(
                            "E_INVALID_INPUT", "uids[] and action=accept|reject required"
                        )
                    results: list[dict[str, Any]] = []
                    for uid in uids:
                        uid_s = str(uid)
                        try:
                            if action == "accept":
                                cs = svc.accept(uid_s)
                            else:
                                cs = svc.reject(uid_s)
                            results.append({"uid": uid_s, "ok": True, "changeset": cs.to_dict()})
                        except CommitError as exc:
                            results.append(
                                {
                                    "uid": uid_s,
                                    "ok": False,
                                    "error": {"code": exc.code, "message": exc.message},
                                }
                            )
                    self._send(200, _ok({"ok": all(r["ok"] for r in results), "results": results}))
                    return
                if path == "/api/pages/content":
                    page_path = str(body.get("path") or "").replace("\\", "/").lstrip("/")
                    content = body.get("content")
                    if not page_path or content is None:
                        raise CommitError("E_INVALID_INPUT", "path and content required")
                    wiki = server_ref.root / "wiki"
                    target = (wiki / page_path).resolve()
                    try:
                        target.relative_to(wiki.resolve())
                    except ValueError:
                        raise CommitError("E_INVALID_INPUT", "path escape") from None
                    from localwiki.core import file_sha256

                    if target.exists():
                        op = {
                            "op": "update",
                            "path": page_path,
                            "content": str(content),
                            "base_sha256": file_sha256(target),
                        }
                    else:
                        op = {"op": "create", "path": page_path, "content": str(content)}
                    cs = svc.create_changeset(
                        purpose=f"ui save {page_path}",
                        ops=[op],
                        state="ready",
                    )
                    cs = svc.accept(cs.change_uid)
                    self._send(200, _ok({"path": page_path, "changeset": cs.to_dict()}))
                    return
                if path == "/api/pages/categories":
                    from localwiki.core import file_sha256
                    from localwiki.wiki_categories import (
                        parse_page_categories,
                        set_categories_in_content,
                        validate_categories,
                    )

                    page_path = str(body.get("path") or "").replace("\\", "/").lstrip("/")
                    cats_in = body.get("categories")
                    if not page_path:
                        raise CommitError("E_INVALID_INPUT", "path required")
                    if not isinstance(cats_in, list):
                        raise CommitError("E_INVALID_INPUT", "categories must be a list")
                    cats = validate_categories([str(c) for c in cats_in])
                    wiki = server_ref.root / "wiki"
                    target = (wiki / page_path).resolve()
                    try:
                        target.relative_to(wiki.resolve())
                    except ValueError:
                        raise CommitError("E_INVALID_INPUT", "path escape") from None
                    if not target.exists():
                        raise CommitError("E_NOT_FOUND", f"page not found: {page_path}")
                    raw = target.read_text(encoding="utf-8", errors="replace")
                    new_content = set_categories_in_content(raw, cats)
                    op = {
                        "op": "update",
                        "path": page_path,
                        "content": new_content,
                        "base_sha256": file_sha256(target),
                    }
                    cs = svc.create_changeset(
                        purpose=f"set categories {page_path}",
                        ops=[op],
                        state="ready",
                    )
                    cs = svc.accept(cs.change_uid)
                    self._send(
                        200,
                        _ok(
                            {
                                "path": page_path,
                                "categories": parse_page_categories(new_content),
                                "changeset": cs.to_dict(),
                            }
                        ),
                    )
                    return
                if path == "/api/settings/workbench-presets":
                    from localwiki.workbench_presets import save_presets

                    out = save_presets(server_ref.root, body if isinstance(body, dict) else {})
                    self._send(200, _ok(out))
                    return
                if path == "/api/settings/workbench-presets/select":
                    from localwiki.workbench_presets import select_preset

                    pid = str(body.get("preset_id") or "")
                    try:
                        out = select_preset(server_ref.root, pid)
                    except ValueError as exc:
                        raise CommitError("E_INVALID_INPUT", str(exc)) from exc
                    self._send(200, _ok(out))
                    return
                if path == "/api/opencode/check-update":
                    from localwiki.runtime.opencode_bridge import check_opencode_update

                    self._send(200, _ok(check_opencode_update()))
                    return
                if path == "/api/import":
                    import base64

                    from localwiki.sources import import_bytes

                    filename = str(body.get("filename") or "upload.md")
                    if body.get("content_b64"):
                        data = base64.b64decode(str(body["content_b64"]))
                    elif body.get("content") is not None:
                        data = str(body["content"]).encode("utf-8")
                    else:
                        raise CommitError("E_INVALID_INPUT", "content or content_b64 required")
                    meta = import_bytes(
                        server_ref.root,
                        data,
                        filename=filename,
                        title=body.get("title"),
                    )
                    self._send(200, _ok(meta))
                    return
                if path == "/api/compile":
                    from localwiki.sources import compile_source

                    source_uid = str(body.get("source_uid") or "")
                    if not source_uid:
                        raise CommitError("E_INVALID_INPUT", "source_uid required")
                    mode = str(body.get("mode") or "model")
                    out = compile_source(
                        server_ref.root,
                        source_uid,
                        version=body.get("version"),
                        mode=mode,
                    )
                    self._send(200, _ok(out))
                    return
                if path == "/api/backup":
                    from localwiki.recovery import backup_workspace

                    dest = str(body.get("dest") or "")
                    if not dest:
                        raise CommitError("E_INVALID_INPUT", "dest required (outside workspace)")
                    out = backup_workspace(
                        server_ref.root,
                        dest,
                        as_zip=bool(body.get("as_zip", True)),
                    )
                    self._send(200, _ok(out))
                    return
                if path == "/api/settings/models":
                    from localwiki.personal_core import save_core_model_settings

                    try:
                        out = save_core_model_settings(server_ref.root, body)
                    except ValueError as exc:
                        raise CommitError("E_INVALID_INPUT", str(exc)) from exc
                    self._send(200, _ok(out))
                    return
                if path == "/api/settings/models/probe":
                    from localwiki.personal_core import probe_core_route

                    route = body.get("route")
                    out = probe_core_route(
                        server_ref.root,
                        str(route) if route else None,
                    )
                    self._send(200, _ok(out))
                    return
                if path == "/api/settings/models/sync":
                    from localwiki.personal_core import sync_core_model_settings

                    try:
                        out = sync_core_model_settings(
                            server_ref.root,
                            source_route=str(body.get("source_route") or "agent"),
                        )
                    except ValueError as exc:
                        raise CommitError("E_INVALID_INPUT", str(exc)) from exc
                    self._send(200, _ok(out))
                    return
                if path == "/api/nightly/run":
                    from localwiki.runtime.nightly import run_nightly

                    out = run_nightly(server_ref.root)
                    self._send(200, _ok(out))
                    return
                if path == "/api/evolution/pause":
                    from localwiki.evolution.gateway import set_pause

                    out = set_pause(server_ref.root, bool(body.get("pause", True)))
                    self._send(200, _ok(out))
                    return
                if path == "/api/evolution/rollback":
                    from localwiki.evolution.gateway import rollback as evo_rollback

                    vid = str(body.get("version_id") or body.get("version") or "")
                    if not vid:
                        raise CommitError("E_INVALID_INPUT", "version_id required")
                    out = evo_rollback(server_ref.root, vid)
                    self._send(200 if out.get("ok") else 404, out if "error" in out else _ok(out))
                    return
                if path == "/api/settings/github-token":
                    from localwiki.skills_hub.github_auth import (
                        clear_github_token,
                        github_token_configured,
                        set_github_token,
                    )

                    if body.get("clear"):
                        clear_github_token(server_ref.root)
                    else:
                        tok = str(body.get("github_token") or body.get("token") or "").strip()
                        if not tok:
                            raise CommitError("E_INVALID_INPUT", "github_token required")
                        set_github_token(server_ref.root, tok)
                    self._send(
                        200,
                        _ok(
                            {
                                "configured": github_token_configured(server_ref.root),
                            }
                        ),
                    )
                    return
                if path == "/api/skills/search":
                    import json as _json
                    import urllib.error
                    import urllib.request

                    from localwiki.skills_hub.github_auth import resolve_github_token
                    from localwiki.skills_hub.search import search_skills

                    q = str(body.get("query") or body.get("q") or "")
                    # R3.12 Q2=A: default online
                    offline = bool(body.get("offline", False))
                    http_get = None
                    if not offline:

                        def http_get(url: str, headers: dict | None = None):  # noqa: ANN001
                            hdrs = {
                                "Accept": "application/vnd.github+json",
                                "User-Agent": "LocalWiki-SkillSearch",
                            }
                            if headers:
                                hdrs.update(headers)
                            req = urllib.request.Request(url, headers=hdrs)
                            try:
                                with urllib.request.urlopen(req, timeout=15) as resp:
                                    raw = resp.read().decode("utf-8", errors="replace")
                                    return _json.loads(raw) if raw.strip() else {}
                            except urllib.error.HTTPError as exc:
                                try:
                                    err_body = exc.read().decode("utf-8", errors="replace")
                                    data = _json.loads(err_body) if err_body.strip() else {}
                                except Exception:  # noqa: BLE001
                                    data = {"message": str(exc)}
                                if not isinstance(data, dict):
                                    data = {"message": str(exc)}
                                data.setdefault("message", str(exc))
                                data.setdefault("status", str(exc.code))
                                return data
                            except Exception as exc:  # noqa: BLE001
                                return {"message": str(exc)}

                    gh_tok = resolve_github_token(server_ref.root)
                    result = search_skills(
                        q,
                        offline=offline,
                        http_get=http_get,
                        github_token=gh_tok,
                        use_cache=True,
                        installable_only=True,
                    )
                    payload = result.to_dict() if hasattr(result, "to_dict") else result
                    if isinstance(payload, dict):
                        payload["github_token_configured"] = bool(gh_tok)
                    self._send(200, _ok(payload))
                    return
                if path == "/api/skills/install":
                    from localwiki.skills_hub.github_auth import resolve_github_token
                    from localwiki.skills_hub.harness import install_skill_or_harness
                    from localwiki.skills_hub.remote import default_http_fetch

                    kind = str(body.get("kind") or "skill")
                    files = body.get("files") or {}
                    source_url = body.get("source_url")
                    repo = body.get("repo")
                    if not (
                        (isinstance(files, dict) and files)
                        or source_url
                        or repo
                    ):
                        raise CommitError(
                            "E_INVALID_INPUT",
                            "files map or source_url/repo required",
                        )
                    out = install_skill_or_harness(
                        server_ref.root,
                        kind=kind,
                        files=files if isinstance(files, dict) else {},
                        source=body.get("source"),
                        source_url=str(source_url) if source_url else None,
                        repo=str(repo) if repo else None,
                        path=str(body["path"]) if body.get("path") else None,
                        ref=str(body["ref"]) if body.get("ref") else None,
                        force=bool(body.get("force")),
                        acknowledge_high_risk=bool(body.get("acknowledge_high_risk")),
                        github_token=resolve_github_token(server_ref.root),
                        http_fetch=default_http_fetch,
                    )
                    self._send(200 if out.get("ok") else 400, _ok(out) if out.get("ok") else out)
                    return
                if path == "/api/skills/bootstrap-office":
                    from localwiki.skills_hub.office_bootstrap import bootstrap_office_skills

                    out = bootstrap_office_skills(
                        server_ref.root, force=bool(body.get("force"))
                    )
                    self._send(200, _ok(out))
                    return
                if path == "/api/exports/materialize":
                    from localwiki.exports import (
                        materialize_docx_from_text,
                        unique_export_name,
                    )

                    text = str(body.get("text") or body.get("content") or "")
                    if not text.strip():
                        raise CommitError("E_INVALID_INPUT", "text required")
                    # R3.15: never reuse a fixed export.docx name by default
                    raw_name = str(body.get("name") or "").strip()
                    if raw_name:
                        name = raw_name
                    else:
                        name = unique_export_name("export", ".docx")
                    try:
                        saved = materialize_docx_from_text(
                            server_ref.root,
                            name,
                            text,
                            force=bool(body.get("force")),
                        )
                    except ValueError as exc:
                        raise CommitError("E_INVALID_INPUT", str(exc)) from exc
                    self._send(200, _ok({"name": saved, "url": f"/api/exports/{saved}"}))
                    return
                if path == "/api/exports/tts":
                    from localwiki.exports import save_bytes, unique_export_name
                    from localwiki.tts_io import TtsError, synthesize_mp3

                    text = str(body.get("text") or body.get("content") or "")
                    if not text.strip():
                        raise CommitError("E_INVALID_INPUT", "text required")
                    stem = str(body.get("name") or body.get("stem") or "speech").strip()
                    voice = str(body.get("voice") or "").strip() or None
                    try:
                        audio = synthesize_mp3(text, voice=voice)
                    except TtsError as exc:
                        raise CommitError(exc.code, exc.message) from exc
                    out_name = unique_export_name(
                        stem.replace(".mp3", "") if stem.lower().endswith(".mp3") else stem,
                        ".mp3",
                    )
                    saved = save_bytes(server_ref.root, out_name, audio)
                    self._send(
                        200,
                        _ok({"name": saved, "url": f"/api/exports/{saved}", "bytes": len(audio)}),
                    )
                    return
                if path == "/api/sessions":
                    from localwiki.sessions import SessionStore

                    meta = SessionStore(server_ref.root).create(
                        title=str(body.get("title") or "") or None,
                        preset_id=str(body.get("preset_id") or "") or None,
                    )
                    self._send(200, _ok(meta))
                    return
                if path == "/api/sessions/clear":
                    from localwiki.sessions import SessionStore

                    if not body.get("confirm"):
                        raise CommitError("E_INVALID_INPUT", "confirm:true required")
                    out = SessionStore(server_ref.root).clear(
                        confirm=True,
                        keep_extracted=bool(body.get("keep_extracted")),
                    )
                    self._send(200, _ok(out))
                    return
                if path.startswith("/api/sessions/") and path.endswith("/messages"):
                    from localwiki.runtime.session_runner import get_runner

                    sid = path[len("/api/sessions/") : -len("/messages")].strip("/")
                    if not sid:
                        raise CommitError("E_INVALID_INPUT", "session id required")
                    content = str(body.get("content") or body.get("message") or "")
                    try:
                        out = get_runner(server_ref.root).enqueue(sid, content)
                    except FileNotFoundError:
                        raise CommitError("E_NOT_FOUND", sid) from None
                    except ValueError as exc:
                        raise CommitError("E_INVALID_INPUT", str(exc)) from exc
                    self._send(200, _ok(out))
                    return
                # R3.26: POST /api/sessions/jobs/{job_id}/stop
                if path.startswith("/api/sessions/jobs/") and path.endswith("/stop"):
                    from localwiki.runtime.session_runner import get_runner

                    mid = path[len("/api/sessions/jobs/") : -len("/stop")].strip("/")
                    if not mid:
                        raise CommitError("E_INVALID_INPUT", "job id required")
                    try:
                        out = get_runner(server_ref.root).cancel_job(mid)
                    except FileNotFoundError:
                        raise CommitError("E_NOT_FOUND", mid) from None
                    self._send(200, _ok(out))
                    return
                if path.startswith("/api/sessions/") and path.endswith("/continue"):
                    from localwiki.runtime.session_runner import get_runner

                    sid = path[len("/api/sessions/") : -len("/continue")].strip("/")
                    if not sid:
                        raise CommitError("E_INVALID_INPUT", "session id required")
                    msg = body.get("message") or body.get("content")
                    msg_s = str(msg) if msg is not None else None
                    try:
                        out = get_runner(server_ref.root).enqueue_continue(
                            sid, message=msg_s
                        )
                    except FileNotFoundError:
                        raise CommitError("E_NOT_FOUND", sid) from None
                    except ValueError as exc:
                        code = "E_NO_OC_SESSION" if "E_NO_OC_SESSION" in str(exc) else "E_INVALID_INPUT"
                        raise CommitError(code, str(exc)) from exc
                    self._send(200, _ok(out))
                    return
                if path.startswith("/api/sessions/") and path.endswith("/extract"):
                    from localwiki.session_extract import extract_after_turn

                    sid = path[len("/api/sessions/") : -len("/extract")].strip("/")
                    if not sid:
                        raise CommitError("E_INVALID_INPUT", "session id required")
                    try:
                        out = extract_after_turn(server_ref.root, sid, force=True)
                    except FileNotFoundError:
                        raise CommitError("E_NOT_FOUND", sid) from None
                    if out.get("ok") is False:
                        err = out.get("error") or {}
                        raise CommitError(
                            str(err.get("code") or "E_MODEL_UNAVAILABLE"),
                            str(err.get("message") or "extract failed"),
                        )
                    self._send(200, _ok(out))
                    return
                if path == "/api/agent/exec":
                    from localwiki.__main__ import run_agent_exec
                    from localwiki.personal_core import build_agent_context, loop_enabled
                    from localwiki.runtime.opencode_bridge import choose_runtime, run as oc_run
                    from localwiki.tools.registry import build_default_registry

                    goal = str(body.get("goal") or "")
                    if not goal:
                        raise CommitError("E_INVALID_INPUT", "goal required")
                    requested = str(body.get("runtime") or "opencode")
                    runtime = choose_runtime(goal, requested)
                    ctx = build_agent_context(server_ref.root, surface="agent")
                    enriched = (
                        f"{goal}\n\n---\nPersonal context:\n{ctx.get('text') or ''}"
                    )
                    if runtime == "opencode":
                        oc = oc_run(enriched, workspace=server_ref.root)
                        payload = {
                            "ok": bool(oc.get("ok")),
                            "runtime": "opencode",
                            "data": oc,
                            "personal_context_meta": ctx.get("meta"),
                            "error": oc.get("error"),
                        }
                        self._send(200 if payload["ok"] else 400, payload)
                        return
                    if not loop_enabled(server_ref.root):
                        self._send(
                            400,
                            _err(
                                "E_DISABLED",
                                "local Agent Loop is off; enable in Settings or use runtime=opencode",
                            ),
                        )
                        return
                    reg = build_default_registry(workspace=server_ref.root)
                    payload, code = run_agent_exec(
                        enriched,
                        workspace=server_ref.root,
                        mode=str(body.get("mode") or "workspace-auto"),
                        registry=reg,
                        as_json=True,
                    )
                    payload["runtime"] = "local"
                    payload["personal_context_meta"] = ctx.get("meta")
                    payload["exit_code"] = code
                    self._send(200 if payload.get("ok") else 400, payload)
                    return
                self._send(501, _err("E_DEP_MISSING", f"not in Personal Core: {path}"))

            def do_DELETE(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if not path.startswith("/api/"):
                    self._send(404, _err("E_NOT_FOUND", path))
                    return
                if not self._require_auth():
                    return
                try:
                    if path.startswith("/api/sessions/"):
                        from localwiki.sessions import SessionStore

                        sid = path[len("/api/sessions/") :].strip("/")
                        if not sid or "/" in sid:
                            raise CommitError("E_INVALID_INPUT", "session id required")
                        try:
                            SessionStore(server_ref.root).delete(sid)
                        except FileNotFoundError:
                            raise CommitError("E_NOT_FOUND", sid) from None
                        self._send(200, _ok({"deleted": sid}))
                        return
                    self._send(501, _err("E_DEP_MISSING", f"not in Personal Core: {path}"))
                except CommitError as exc:
                    status = 404 if exc.code == "E_NOT_FOUND" else 400
                    self._send(status, _err(exc.code, exc.message))
                except Exception as exc:  # noqa: BLE001
                    self._send(500, _err("E_FAILED", str(exc), recoverable=False))

        return Handler

    def serve_forever(self) -> None:
        if not self.acquire_runtime_lock():
            raise RuntimeError("workspace already locked by another process")
        try:
            handler = self.make_handler()
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self._httpd.serve_forever()
        finally:
            self.release_runtime_lock()

    def shutdown(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self.release_runtime_lock()


def run_server(
    workspace: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> None:
    srv = LocalWikiServer(workspace, host=host, port=port)
    print(
        json.dumps(
            {
                "ok": True,
                "message": f"LocalWiki serving http://{srv.host}:{srv.port}/",
                "workspace": str(srv.root),
                "token": srv.token,
                "token_hint": (srv.token[:4] + "…" if srv.token else None),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    print(f"LocalWiki Token (paste in Settings): {srv.token}", flush=True)
    print(f"Open http://{srv.host}:{srv.port}/  — or use scripts/start-localwiki.cmd", flush=True)
    try:
        from localwiki.skills_hub.office_bootstrap import bootstrap_office_skills, skills_empty

        if skills_empty(srv.root):
            boot = bootstrap_office_skills(srv.root, force=False)
            print(
                json.dumps(
                    {"ok": True, "message": "office skills bootstrap", "data": boot},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": f"office skills bootstrap skipped: {exc}",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    srv.serve_forever()
