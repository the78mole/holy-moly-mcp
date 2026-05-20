import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services import converter
from src.services.converter import ModelPhase, TranscriptionMode, _ModelState


def _make_ready_state(transcript: str = "hello world") -> _ModelState:
    """Return a pre-built ModelState with a mock WhisperModel instance."""
    mock_segment = MagicMock()
    mock_segment.text = transcript
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], MagicMock())
    return _ModelState(
        phase=ModelPhase.READY,
        progress=100,
        message="ready",
        instance=mock_model,
    )


class ConverterServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_audio_minutes_mode_logs_warning(self) -> None:
        ready_state = _make_ready_state("hello world")

        with (
            patch.object(converter, "_state", ready_state),
            patch.object(converter, "_load_started", True),
        ):
            stderr = StringIO()
            with redirect_stderr(stderr):
                result = await converter.process_audio(b"audio-bytes", "meeting.wav", "minutes")

        self.assertEqual(result, "hello world")
        self.assertIn("minutes", stderr.getvalue().lower())

    async def test_process_audio_rejects_unsupported_extension(self) -> None:
        with self.assertRaises(ValueError):
            await converter.process_audio(b"audio-bytes", "notes.txt", TranscriptionMode.PLAIN)

    async def test_process_audio_from_path_rejects_relative_path(self) -> None:
        with self.assertRaises(ValueError):
            await converter.process_audio_from_path("relative/file.wav", "plain")

    async def test_process_audio_plain_transcription(self) -> None:
        ready_state = _make_ready_state("test transcript")

        with (
            patch.object(converter, "_state", ready_state),
            patch.object(converter, "_load_started", True),
        ):
            result = await converter.process_audio(b"audio-bytes", "clip.wav", "plain")

        self.assertEqual(result, "test transcript")


if __name__ == "__main__":
    unittest.main()
