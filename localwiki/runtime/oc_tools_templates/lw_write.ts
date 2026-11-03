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
    "Write or append a (possibly large) text file under the LocalWiki workspace. Prefer this over built-in write for HTML/CSS/JS demos and big files. Content is sent via a temp file to avoid truncation.",
  args: {
    path: tool.schema
      .string()
      .describe("Relative path under workspace, e.g. .kw/demo/index.html"),
    content: tool.schema.string().describe("Full file text to write"),
    mode: tool.schema
      .string()
      .optional()
      .describe('Optional "write" (default) or "append"'),
  },
  async execute(args, context) {
    const ws =
      process.env.LOCALWIKI_WORKSPACE ||
      context.directory ||
      context.worktree ||
      process.cwd()
    const py = resolvePython()
    const rel = String(args.path || "").trim()
    if (!rel) return "lw_write failed: path required"
    const mode = String(args.mode || "write").toLowerCase()
    const tmp = path.join(
      os.tmpdir(),
      `lw_write_${Date.now()}_${Math.random().toString(16).slice(2)}.txt`,
    )
    fs.writeFileSync(tmp, String(args.content ?? ""), "utf8")
    try {
      const argv = [
        "-m",
        "localwiki.tools.lw_write_cli",
        "--path",
        rel,
        "--content-file",
        tmp,
      ]
      if (mode === "append") argv.push("--append")
      const r = spawnSync(py, argv, {
        cwd: ws,
        env: { ...process.env, LOCALWIKI_WORKSPACE: ws },
        encoding: "utf-8",
        maxBuffer: 16 * 1024 * 1024,
        timeout: 120000,
      })
      const out = ((r.stdout || "") + (r.stderr || "")).trim()
      if (r.status !== 0) {
        return `lw_write failed: ${out || r.error || "unknown"}`
      }
      return out || "lw_write: ok"
    } finally {
      try {
        fs.unlinkSync(tmp)
      } catch {
        /* ignore */
      }
    }
  },
})
