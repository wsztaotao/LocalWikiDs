"""Local text-to-speech via edge-tts (R3.15)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
_FALLBACK_VOICES = (
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-XiaoyiNeural",
)


class TtsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _transient_tts_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "429",
            "503",
            "502",
            "timeout",
            "timed out",
            "temporarily",
            "invalid response status",
            "websocket",
            "connection reset",
            "network",
        )
    )


def synthesize_mp3(
    text: str,
    *,
    voice: str | None = None,
    retries: int = 4,
) -> bytes:
    """Synthesize ``text`` to MP3 bytes. Raises ``TtsError`` on failure."""
    body = (text or "").strip()
    if not body:
        raise TtsError("E_INVALID_INPUT", "text required")
    if len(body) > 20000:
        body = body[:20000]
    preferred = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    voices: list[str] = [preferred]
    for v in _FALLBACK_VOICES:
        if v not in voices:
            voices.append(v)
    try:
        import edge_tts  # type: ignore
    except ImportError as exc:
        raise TtsError(
            "E_DEP_MISSING",
            "edge-tts not installed; pip install edge-tts into work venv",
        ) from exc

    last_err: BaseException | None = None
    attempts = max(1, int(retries))
    for i in range(attempts):
        v = voices[i % len(voices)]

        async def _run(chosen: str = v) -> bytes:
            communicate = edge_tts.Communicate(body, chosen)
            chunks: list[bytes] = []
            async for item in communicate.stream():
                if item.get("type") == "audio":
                    data = item.get("data") or b""
                    if data:
                        chunks.append(data)
            return b"".join(chunks)

        try:
            try:
                audio = asyncio.run(_run())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    audio = loop.run_until_complete(_run())
                finally:
                    loop.close()
        except TtsError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if i + 1 < attempts and _transient_tts_error(exc):
                time.sleep(1.2 * (i + 1))
                continue
            raise TtsError("E_TTS_FAILED", f"edge-tts failed: {exc}") from exc
        if audio and len(audio) >= 32:
            return audio
        last_err = TtsError("E_TTS_FAILED", "edge-tts returned empty audio")
        if i + 1 < attempts:
            time.sleep(0.8 * (i + 1))
            continue
    if isinstance(last_err, TtsError):
        raise last_err
    raise TtsError("E_TTS_FAILED", f"edge-tts failed: {last_err}")


def synthesize_mp3_result(text: str, *, voice: str | None = None) -> dict[str, Any]:
    """Same as synthesize_mp3 but returns ok/error dict (API-friendly)."""
    try:
        data = synthesize_mp3(text, voice=voice)
        return {"ok": True, "bytes": data, "voice": voice or DEFAULT_VOICE}
    except TtsError as exc:
        return {
            "ok": False,
            "error": {"code": exc.code, "message": exc.message},
        }
