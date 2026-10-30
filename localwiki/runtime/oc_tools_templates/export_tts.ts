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
    "Synthesize local MP3 speech via LocalWiki tts_io (edge-tts), save under .kw/exports, return /api/exports/<name>.mp3. Use for 语音/音频/朗读/TTS download.",
  args: {
    text: tool.schema.string().describe("Text to speak"),
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
    const stem = args.stem || "speech"
    const tmp = path.join(
      os.tmpdir(),
      `lw_tts_${Date.now()}_${Math.random().toString(16).slice(2)}.txt`,
    )
    fs.writeFileSync(tmp, String(args.text || ""), "utf8")
    try {
      const r = spawnSync(
        py,
        [
          "-m",
          "localwiki.tools.oc_export_cli",
          "tts",
          "--text-file",
          tmp,
          "--stem",
          String(stem),
        ],
        {
          cwd: ws,
          env: { ...process.env, LOCALWIKI_WORKSPACE: ws },
          encoding: "utf-8",
          maxBuffer: 16 * 1024 * 1024,
          timeout: 180000,
        },
      )
      const out = ((r.stdout || "") + (r.stderr || "")).trim()
      if (r.status !== 0) {
        return `export_tts failed: ${out || r.error || "unknown"}`
      }
      const line = out
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean)
        .pop()
      return line || out || "export_tts: no output"
    } finally {
      try {
        fs.unlinkSync(tmp)
      } catch {
        /* ignore */
      }
    }
  },
})
