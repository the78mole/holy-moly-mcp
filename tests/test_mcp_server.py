import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp_server import SpeechToTextRequest, SpeechToTextService, TranscriptionMode


class SpeechToTextRequestTests(unittest.TestCase):
    def test_requires_exactly_one_source(self) -> None:
        with self.assertRaises(ValueError):
            SpeechToTextRequest()

        with self.assertRaises(ValueError):
            SpeechToTextRequest(file_path="/tmp/example.wav", url="https://example.com/a.wav")

    def test_rejects_non_absolute_file_path(self) -> None:
        with self.assertRaises(ValueError):
            SpeechToTextRequest(file_path="relative/audio.wav")


class SpeechToTextServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_minutes_mode_logs_warning_and_returns_text(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            create_mock = AsyncMock(return_value=SimpleNamespace(text="transcribed text"))
            service = SpeechToTextService(api_key="test-api-key")
            service._client = SimpleNamespace(
                audio=SimpleNamespace(
                    transcriptions=SimpleNamespace(create=create_mock),
                )
            )

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = await service.transcribe(Path(audio_file.name), TranscriptionMode.MINUTES)

            self.assertEqual(result, "transcribed text")
            self.assertIn("minutes", stderr.getvalue().lower())

    async def test_rejects_unsupported_extension(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".txt") as audio_file:
            service = SpeechToTextService(api_key="test-api-key")
            service._client = SimpleNamespace(
                audio=SimpleNamespace(
                    transcriptions=SimpleNamespace(create=AsyncMock()),
                )
            )

            with self.assertRaises(ValueError):
                await service.transcribe(Path(audio_file.name), TranscriptionMode.PLAIN)


if __name__ == "__main__":
    unittest.main()
