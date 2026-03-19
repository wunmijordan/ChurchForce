"""
music/api_clients/audio.py

OpenAI Whisper integration for audio transcription.

Transcribes uploaded audio reference files (MP3/WAV) into lyrics text,
which is then stored on the Track model. Useful when a music unit wants
to add a song to the library from an audio recording rather than typing
out lyrics manually.

Required settings:
    OPENAI_API_KEY

Note: stem separation (splitting audio into vocals/instruments) has been
removed from the original stub — Whisper is a transcription model, not
a stem separator. Proper stem separation requires a different tool like
Demucs or Spleeter. A stub for that is left as a TODO.
"""

import logging
import os
from django.conf import settings

logger = logging.getLogger(__name__)


class AudioProcessorError(Exception):
    pass


class AudioProcessor:

    def __init__(self):
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        if not api_key:
            raise AudioProcessorError("OPENAI_API_KEY must be set in settings.")

        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key)
        except ImportError:
            raise AudioProcessorError(
                "openai package is not installed. Run: pip install openai"
            )

    def transcribe(self, file_path):
        """
        Transcribe an audio file to text using OpenAI Whisper.

        Returns the transcription text string.
        Raises AudioProcessorError on failure.
        """
        if not os.path.exists(file_path):
            raise AudioProcessorError(f"File not found: {file_path}")

        try:
            with open(file_path, "rb") as audio_file:
                result = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                )
            return result
        except Exception as exc:
            raise AudioProcessorError(f"Whisper transcription failed: {exc}") from exc

    def transcribe_to_srt(self, file_path):
        """
        Transcribe with timestamps in SRT subtitle format.
        Useful for song lyric timing displays.
        """
        if not os.path.exists(file_path):
            raise AudioProcessorError(f"File not found: {file_path}")

        try:
            with open(file_path, "rb") as audio_file:
                result = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="srt",
                )
            return result
        except Exception as exc:
            raise AudioProcessorError(f"Whisper SRT transcription failed: {exc}") from exc

    # TODO: Implement stem separation using Demucs or Spleeter
    # These are local Python libraries, not OpenAI APIs.
    # def extract_stems(self, file_path): ...