import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services import converter
from src.services.converter import TranscriptionMode


class ConverterServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_audio_minutes_mode_logs_warning(self) -> None:
        fake_client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(
                    create=AsyncMock(return_value=SimpleNamespace(text="hello world"))
                )
            )
        )

        with patch("src.services.converter._openai_client", return_value=fake_client):
            stderr = io.StringIO()
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


if __name__ == "__main__":
    unittest.main()
