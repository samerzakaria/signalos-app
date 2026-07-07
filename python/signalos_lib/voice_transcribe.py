"""Voice transcription for the desktop composer (#21).

The WebView2 frontend records a short audio clip (webm/opus via
MediaRecorder), base64-encodes it, and sends it over IPC as
``voice:transcribe {"audio_b64": ..., "mime": ...}``. This module picks the
first configured provider that can transcribe audio and POSTs the clip to
its OpenAI-compatible ``/audio/transcriptions`` endpoint.

Transcription-capable providers (a strict subset of the harness provider
table — most chat providers have no speech endpoint):

  - OpenAI  (``OPENAI_API_KEY``)  → whisper-1
  - Groq    (``GROQ_API_KEY``)    → whisper-large-v3

Privacy invariants:
  - Audio is NEVER logged and NEVER persisted — it exists only in memory for
    the duration of the request. Error messages must not embed payload bytes.
  - A hard size cap (``MAX_AUDIO_BYTES``) bounds memory and upload time.

All domain outcomes are reported as ``{"status": ..., ...}`` dicts (the
capability-wiring contract used by the other IPC handlers):
  ok / no-capable-provider / invalid-audio / too-large / provider-error
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request

# ~10 MB of decoded audio. 60s of webm/opus is well under 1 MB, so this cap
# only trips on abuse or a runaway recorder — never a normal dictation.
MAX_AUDIO_BYTES = 10 * 1024 * 1024

# Per-request network budget. Whisper on a <60s clip returns in seconds.
HTTP_TIMEOUT_SECONDS = 60.0

NO_PROVIDER_MESSAGE = "Voice transcription needs an OpenAI or Groq key."


@dataclass(frozen=True)
class TranscribeProvider:
    """One provider that exposes an OpenAI-compatible transcription API."""

    name: str
    env_var: str
    url: str
    model: str


# Priority order mirrors the harness provider table (openai before groq).
TRANSCRIBE_PROVIDERS: tuple[TranscribeProvider, ...] = (
    TranscribeProvider(
        "openai", "OPENAI_API_KEY",
        "https://api.openai.com/v1/audio/transcriptions", "whisper-1",
    ),
    TranscribeProvider(
        "groq", "GROQ_API_KEY",
        "https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3",
    ),
)


def pick_provider() -> TranscribeProvider | None:
    """First transcription-capable provider whose API key is configured."""
    for spec in TRANSCRIBE_PROVIDERS:
        if os.environ.get(spec.env_var, "").strip():
            return spec
    return None


# Map the recorder's MIME type onto a filename the endpoint recognizes.
# Whisper sniffs content but keys the container format off the extension.
_MIME_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
}


def _normalize_mime(mime: str) -> str:
    """Strip codec parameters: ``audio/webm;codecs=opus`` → ``audio/webm``."""
    return (mime or "").split(";", 1)[0].strip().lower() or "audio/webm"


def _build_multipart(
    audio: bytes, mime: str, model: str
) -> tuple[bytes, str]:
    """Encode the model field + audio file as multipart/form-data."""
    boundary = "signalos-voice-" + uuid.uuid4().hex
    ext = _MIME_EXTENSIONS.get(mime, "webm")
    parts: list[bytes] = []
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="model"\r\n\r\n'
            f"{model}\r\n"
        ).encode("utf-8")
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="recording.{ext}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def _decode_audio(audio_b64: str) -> bytes:
    """Decode the payload; tolerate a data-URL prefix from FileReader."""
    raw = (audio_b64 or "").strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    # The JS btoa/FileReader output never contains whitespace, but be
    # lenient about newlines a transport may have introduced.
    raw = raw.replace("\n", "").replace("\r", "")
    return base64.b64decode(raw, validate=True)


def _provider_error_detail(body: bytes) -> str:
    """Extract the provider's human message from an error response body."""
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return body.decode("utf-8", errors="replace")[:300].strip()
    if isinstance(parsed, dict):
        err_obj = parsed.get("error")
        if isinstance(err_obj, dict) and err_obj.get("message"):
            return str(err_obj["message"])
        if isinstance(err_obj, str) and err_obj:
            return err_obj
        if parsed.get("message"):
            return str(parsed["message"])
    return str(parsed)[:300]


def transcribe(audio_b64: str, mime: str = "audio/webm") -> dict:
    """Transcribe a base64 audio clip. Returns a status dict, never raises.

    The audio bytes live only in this frame — they are not logged, not
    written to disk, and not echoed back in any error path.
    """
    try:
        audio = _decode_audio(audio_b64)
    except (ValueError, TypeError):
        return {
            "status": "invalid-audio",
            "error": "The recording payload was not valid base64 audio.",
        }
    if not audio:
        return {
            "status": "invalid-audio",
            "error": "The recording was empty — nothing to transcribe.",
        }
    if len(audio) > MAX_AUDIO_BYTES:
        mb = len(audio) / (1024 * 1024)
        cap_mb = MAX_AUDIO_BYTES // (1024 * 1024)
        return {
            "status": "too-large",
            "error": (
                f"The recording is {mb:.1f} MB — the cap is {cap_mb} MB. "
                "Record a shorter clip."
            ),
        }

    provider = pick_provider()
    if provider is None:
        return {"status": "no-capable-provider", "error": NO_PROVIDER_MESSAGE}

    body, boundary = _build_multipart(audio, _normalize_mime(mime), provider.model)
    request = urllib_request.Request(
        provider.url,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ[provider.env_var].strip()}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except urllib_error.HTTPError as exc:
        detail = _provider_error_detail(exc.read() or b"")
        return {
            "status": "provider-error",
            "provider": provider.name,
            "error": (
                f"{provider.name} transcription failed (HTTP {exc.code})"
                + (f": {detail}" if detail else "")
            ),
        }
    except (urllib_error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        return {
            "status": "provider-error",
            "provider": provider.name,
            "error": f"Could not reach {provider.name} for transcription: {reason}",
        }

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {
            "status": "provider-error",
            "provider": provider.name,
            "error": f"{provider.name} returned an unreadable transcription response.",
        }
    text = parsed.get("text") if isinstance(parsed, dict) else None
    if not isinstance(text, str):
        return {
            "status": "provider-error",
            "provider": provider.name,
            "error": f"{provider.name} returned no transcript text.",
        }
    return {
        "status": "ok",
        "text": text.strip(),
        "provider": provider.name,
        "model": provider.model,
    }
