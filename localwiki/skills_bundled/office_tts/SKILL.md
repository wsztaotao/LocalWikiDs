---
name: office-tts
description: Local text-to-speech via LocalWiki tts_io (edge-tts); save MP3 under .kw/exports for download.
---

# Office TTS (local)

## When to use

User asks to convert text / essay / reply into audio, speech, voice, 朗读, 语音, or MP3 download.

## How

1. Prefer Python in the workspace venv:

```python
from pathlib import Path
from localwiki.tts_io import synthesize_mp3
from localwiki.exports import save_bytes, unique_export_name

ws = Path(r"<workspace>")  # LOCALWIKI_WORKSPACE
audio = synthesize_mp3("要朗读的正文")
name = unique_export_name("speech", ".mp3")
save_bytes(ws, name, audio)
print("/api/exports/" + name)
```

2. Always write under `<workspace>/.kw/exports/<unique>.mp3`.
3. Reply with exactly `/api/exports/<unique>.mp3` after a successful write.
4. Do **not** claim the environment has no TTS tools. Do **not** send users only to external TTS websites when this skill is installed.
5. If edge-tts fails (network / missing package), say the readable error — never invent a fake download link.
