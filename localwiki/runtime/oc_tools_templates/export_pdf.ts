import { tool } from "@opencode-ai/plugin"
import { spawnSync } from "child_process"
import fs from "fs"
import os from "os"
import path from "path"

function resolvePython(): string {
  if (process.env.LOCALWIKI_PYTHON) return process.env.LOCALWIKI_PYTHON
  return process.platform === "win32" ? "python" : "python3"
}

export default tool({
  description:
    "Write a PDF under LocalWiki .kw/exports and return /api/exports/<name>.pdf. Use whenever the user asks for PDF download. Prefer this over ad-hoc Python; do not claim Python is missing.",
  args: {
    title: tool.schema.string().describe("Document title"),
    paragraphs: tool.schema
      .string()
      .describe("Body text; separate paragraphs with blank lines"),
    stem: tool.schema
      .string()
      .optional()
      .describe("Optional filename stem (unique suffix added)"),
  },
  async execute(args, context) {
    const ws =
      process.env.LOCALWIKI_WORKSPACE ||
      context.directory ||
      context.worktree ||
      process.cwd()
    const py = resolvePython()
    const stem = args.stem || "export"
    const tmp = path.join(
      os.tmpdir(),
      `lw_pdf_${Date.now()}_${Math.random().toString(16).slice(2)}.txt`,
    )
    fs.writeFileSync(tmp, String(args.paragraphs || ""), "utf8")
    try {
      const r = spawnSync(
        py,
        [
          "-m",
          "localwiki.tools.oc_export_cli",
          "pdf",
          "--title",
          String(args.title || "Document"),
          "--body-file",
          tmp,
          "--stem",
          String(stem),
        ],
        {
          cwd: ws,
          env: { ...process.env, LOCALWIKI_WORKSPACE: ws },
          encoding: "utf-8",
          maxBuffer: 16 * 1024 * 1024,
          timeout: 120000,
        },
      )
      const out = ((r.stdout || "") + (r.stderr || "")).trim()
      if (r.status !== 0) {
        return `export_pdf failed: ${out || r.error || "unknown"}`
      }
      const line = out
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean)
        .pop()
      return line || out || "export_pdf: no output"
    } finally {
      try {
        fs.unlinkSync(tmp)
      } catch {
        /* ignore */
      }
    }
  },
})
