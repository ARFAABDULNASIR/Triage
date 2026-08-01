"""Speech-to-text for meeting recordings — free at every step.

Order of preference:
  1. Groq Whisper API (whisper-large-v3-turbo) — fast, free tier, needs GROQ_API_KEY.
     Video and oversized files are converted to compact mono audio first (pydub/ffmpeg).
  2. Local faster-whisper — CPU-friendly, fully offline. Decodes audio AND video
     directly via PyAV, so it also covers machines without ffmpeg installed.
"""

import logging
import os
import tempfile

from django.conf import settings

from integrations.llm_providers import groq_available
from sessions_app.media_utils import is_video_file

logger = logging.getLogger(__name__)

GROQ_WHISPER_MODEL = "whisper-large-v3-turbo"
MAX_GROQ_UPLOAD_BYTES = 24 * 1024 * 1024

_local_model = None


def transcribe(media_path: str, on_progress=None) -> tuple[str, str]:
    """Transcribe an audio or video file. Returns (transcript, engine name)."""
    notify = on_progress or (lambda msg: None)
    errors: list[str] = []

    if groq_available():
        try:
            notify("Transcribing with Groq Whisper…")
            return _transcribe_groq(media_path), "groq-whisper"
        except Exception as exc:
            logger.warning("Groq transcription failed, trying local whisper: %s", exc)
            errors.append(f"Groq Whisper: {str(exc)[:200]}")

    try:
        notify("Transcribing locally with Whisper. The first run downloads the model and can take a few minutes…")
        return _transcribe_local(media_path), "faster-whisper"
    except ImportError:
        errors.append(
            "Local fallback unavailable. Install it with: pip install faster-whisper"
        )
    except Exception as exc:
        errors.append(f"Local whisper: {str(exc)[:200]}")

    raise RuntimeError("Could not transcribe this recording. " + " · ".join(errors))


def _audio_for_upload(media_path: str) -> tuple[str, bool]:
    """Return (path, is_temp) with audio suitable for the Groq API (≤ ~25MB)."""
    if not is_video_file(media_path) and os.path.getsize(media_path) <= MAX_GROQ_UPLOAD_BYTES:
        return media_path, False

    from pydub import AudioSegment

    audio = AudioSegment.from_file(media_path).set_frame_rate(16000).set_channels(1)
    fd, out_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    audio.export(out_path, format="mp3", bitrate="48k")
    return out_path, True


def _transcribe_groq(media_path: str) -> str:
    from groq import Groq

    audio_path, is_temp = _audio_for_upload(media_path)
    try:
        if os.path.getsize(audio_path) > MAX_GROQ_UPLOAD_BYTES:
            raise RuntimeError("Recording is too long for the Groq API upload limit")
        client = Groq(api_key=settings.GROQ_API_KEY.strip())
        with open(audio_path, "rb") as f:
            result = client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), f),
                model=GROQ_WHISPER_MODEL,
                response_format="text",
            )
        text = (result if isinstance(result, str) else getattr(result, "text", "")).strip()
        if not text:
            raise RuntimeError("The recording appears to contain no speech")
        return text
    finally:
        if is_temp and os.path.exists(audio_path):
            os.remove(audio_path)


def _get_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading faster-whisper model '%s' (first run downloads it)", settings.FASTER_WHISPER_MODEL)
        _local_model = WhisperModel(settings.FASTER_WHISPER_MODEL, device="cpu", compute_type="int8")
    return _local_model


def _transcribe_local(media_path: str) -> str:
    segments, _info = _get_local_model().transcribe(media_path, vad_filter=True)
    text = "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
    if not text:
        raise RuntimeError("The recording appears to contain no speech")
    return text
