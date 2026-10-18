"""Model cache detection and removal (STT via HF hub cache, TTS via Kokoro files)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holy_moly_server.services import converter


def _make_snapshot(hub: Path, repo: str, complete: bool = True) -> Path:
    d = hub / ("models--" + repo.replace("/", "--"))
    snap = d / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    if complete:
        (snap / "model.bin").write_bytes(b"\0" * 2048)
    (d / "refs").mkdir()
    (d / "blobs").mkdir()
    return d


class ModelCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = Path(self.tmp.name) / "hub"
        self.hub.mkdir()
        self._env = patch.dict(os.environ, {"HF_HOME": self.tmp.name})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self.tmp.cleanup()

    def test_repo_mapping_follows_faster_whisper(self) -> None:
        # large-v3-turbo is published by mobiuslabsgmbh, not Systran; the old guess missed it.
        self.assertEqual(converter._model_hf_repo("large-v3-turbo"), "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        self.assertEqual(converter._model_hf_repo("base"), "Systran/faster-whisper-base")
        self.assertEqual(converter._model_hf_repo("distil-large-v3"), "Systran/faster-distil-whisper-large-v3")

    def test_cached_requires_complete_snapshot(self) -> None:
        _make_snapshot(self.hub, "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        _make_snapshot(self.hub, "Systran/faster-whisper-small", complete=False)
        self.assertTrue(converter.is_model_cached("large-v3-turbo"))
        self.assertFalse(converter.is_model_cached("small"))
        self.assertFalse(converter.is_model_cached("medium"))
        self.assertEqual(converter.get_model_cache_size_mb("large-v3-turbo"), 0)  # 2 KB rounds to 0 MB
        self.assertEqual(converter.get_model_cache_size_mb("medium"), 0)

    def test_delete_refuses_active_model_and_removes_others(self) -> None:
        d = _make_snapshot(self.hub, "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        (self.hub / ".locks" / d.name).mkdir(parents=True)
        with patch.object(converter, "get_active_model_name", return_value="large-v3-turbo"):
            with self.assertRaises(ValueError):
                converter.delete_model_cache("large-v3-turbo")
        self.assertTrue(d.exists())
        with patch.object(converter, "get_active_model_name", return_value="base"):
            converter.delete_model_cache("large-v3-turbo")
            with self.assertRaises(FileNotFoundError):
                converter.delete_model_cache("large-v3-turbo")
        self.assertFalse(d.exists())
        self.assertFalse((self.hub / ".locks" / d.name).exists())


class TtsModelCacheTests(unittest.TestCase):
    def test_delete_kokoro_file_keeps_voices(self) -> None:
        from holy_moly_server.services import tts_local

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "kokoro"
            cache.mkdir()
            (cache / "kokoro-v1.0.fp16.onnx").write_bytes(b"\0" * 1024)
            (cache / "voices-v1.0.bin").write_bytes(b"\0" * 16)
            with patch.object(tts_local, "KOKORO_CACHE_DIR", cache), patch.object(tts_local, "_active_tts_model", "kokoro-v1.0-int8"):
                self.assertTrue(tts_local.is_tts_model_cached("kokoro-v1.0-fp16"))
                tts_local.delete_tts_model_cache("kokoro-v1.0-fp16")
                self.assertFalse(tts_local.is_tts_model_cached("kokoro-v1.0-fp16"))
                self.assertTrue((cache / "voices-v1.0.bin").exists())
                with self.assertRaises(FileNotFoundError):
                    tts_local.delete_tts_model_cache("kokoro-v1.0-fp16")
                with self.assertRaises(ValueError):
                    tts_local.delete_tts_model_cache("kokoro-v1.0-int8")  # active
                with self.assertRaises(ValueError):
                    tts_local.delete_tts_model_cache("nope")


if __name__ == "__main__":
    unittest.main()
