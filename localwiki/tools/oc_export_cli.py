"""CLI for OpenCode custom tools: export docx / tts / pdf (R3.16 / R3.17).

Usage:
  python -m localwiki.tools.oc_export_cli docx --title T --body B [--stem s]
  python -m localwiki.tools.oc_export_cli tts --text T [--stem s]
  python -m localwiki.tools.oc_export_cli pdf --title T --body-file path [--stem s]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _workspace() -> Path:
    env = os.environ.get("LOCALWIKI_WORKSPACE") or ""
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def _read_maybe_file(direct: str, file_path: str | None) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return direct or ""


def _paragraphs_from_body(body: str) -> list[str]:
    from localwiki.runtime.export_plain import strip_markdown_for_export

    clean = strip_markdown_for_export(body)
    paragraphs = [p.strip() for p in clean.replace("\r\n", "\n").split("\n\n") if p.strip()]
    if not paragraphs and clean.strip():
        paragraphs = [clean.strip()]
    return paragraphs


def cmd_docx(args: argparse.Namespace) -> int:
    from localwiki.exports import save_bytes, unique_export_name
    from localwiki.office_io import write_docx
    from localwiki.runtime.export_plain import strip_markdown_for_export

    title = strip_markdown_for_export(args.title or "").strip() or "Document"
    body = _read_maybe_file(
        args.body if args.body is not None else "",
        getattr(args, "body_file", None),
    )
    paragraphs = _paragraphs_from_body(body)
    if not paragraphs:
        print("empty body", file=sys.stderr)
        return 2
    stem = (args.stem or "export").strip() or "export"
    name = unique_export_name(stem, ".docx")
    data = write_docx(title=title, paragraphs=paragraphs)
    ws = _workspace()
    saved = save_bytes(ws, name, data)
    print(f"/api/exports/{saved}")
    return 0


def cmd_tts(args: argparse.Namespace) -> int:
    from localwiki.exports import save_bytes, unique_export_name
    from localwiki.runtime.export_plain import strip_markdown_for_export
    from localwiki.tts_io import TtsError, synthesize_mp3

    text = strip_markdown_for_export(
        _read_maybe_file(args.text or "", getattr(args, "text_file", None))
    ).strip()
    if not text:
        print("empty text", file=sys.stderr)
        return 2
    stem = (args.stem or "speech").strip() or "speech"
    try:
        audio = synthesize_mp3(text)
    except TtsError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    name = unique_export_name(stem, ".mp3")
    saved = save_bytes(_workspace(), name, audio)
    print(f"/api/exports/{saved}")
    return 0


def cmd_pdf(args: argparse.Namespace) -> int:
    from localwiki.exports import save_bytes, unique_export_name
    from localwiki.office_io import write_pdf
    from localwiki.runtime.export_plain import strip_markdown_for_export

    title = strip_markdown_for_export(args.title or "").strip() or "Document"
    body = _read_maybe_file(
        args.body if args.body is not None else "",
        getattr(args, "body_file", None),
    )
    paragraphs = _paragraphs_from_body(body)
    if not paragraphs:
        print("empty body", file=sys.stderr)
        return 2
    stem = (args.stem or "export").strip() or "export"
    try:
        data = write_pdf(title=title, paragraphs=paragraphs)
    except ImportError as exc:
        print(f"E_DEP_MISSING: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"E_PDF_FAILED: {exc}", file=sys.stderr)
        return 1
    name = unique_export_name(stem, ".pdf")
    saved = save_bytes(_workspace(), name, data)
    print(f"/api/exports/{saved}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="oc_export_cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("docx")
    d.add_argument("--title", default="Document")
    d.add_argument("--body", default="")
    d.add_argument("--body-file", dest="body_file", default=None)
    d.add_argument("--stem", default="export")
    d.set_defaults(func=cmd_docx)

    t = sub.add_parser("tts")
    t.add_argument("--text", default="")
    t.add_argument("--text-file", dest="text_file", default=None)
    t.add_argument("--stem", default="speech")
    t.set_defaults(func=cmd_tts)

    pdf = sub.add_parser("pdf")
    pdf.add_argument("--title", default="Document")
    pdf.add_argument("--body", default="")
    pdf.add_argument("--body-file", dest="body_file", default=None)
    pdf.add_argument("--stem", default="export")
    pdf.set_defaults(func=cmd_pdf)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
