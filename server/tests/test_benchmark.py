"""STT benchmark: result maths, storage, and the guards of start_benchmark."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holy_moly_server.services import benchmark, converter


class BenchmarkMathTests(unittest.TestCase):
    def test_make_result_scales_to_one_minute(self) -> None:
        r = benchmark.make_result("base", "cpu", "int8", audio_seconds=15.0, best_seconds=6.0)
        self.assertEqual(r.seconds_per_audio_minute, 24.0)
        self.assertEqual(r.realtime_factor, 0.4)
        self.assertGreater(r.cpu_count, 0)

    def test_measure_keeps_the_fastest_pass(self) -> None:
        calls = []

        def run() -> float:
            calls.append(1)
            return 15.0

        best = benchmark.measure(run, passes=3)
        self.assertEqual(len(calls), 3)
        self.assertGreaterEqual(best, 0.0)

    def test_sample_is_bundled(self) -> None:
        self.assertTrue(benchmark.SAMPLE_PATH.exists(), benchmark.SAMPLE_PATH)
        self.assertGreater(benchmark.SAMPLE_PATH.stat().st_size, 100_000)


class BenchmarkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = benchmark.BenchmarkStore(Path(self.tmp.name) / "b.sqlite3")
        benchmark.set_store(self.store)

    def tearDown(self) -> None:
        benchmark.set_store(None)
        self.store.close()
        self.tmp.cleanup()

    def test_save_replaces_per_model_and_hardware(self) -> None:
        self.store.save(benchmark.make_result("base", "cpu", "int8", 15, 6))
        self.store.save(benchmark.make_result("base", "cpu", "int8", 15, 3))   # newer measurement wins
        self.store.save(benchmark.make_result("base", "cuda", "float16", 15, 1))
        self.assertEqual(len(self.store.all()), 2)
        self.assertEqual(self.store.get("base", "cpu", "int8").seconds_per_audio_minute, 12.0)
        with patch.dict("os.environ", {"HOLY_MOLY_DEVICE": "cpu", "HOLY_MOLY_COMPUTE_TYPE": "int8"}):
            current = benchmark.results_for_current_hardware()
        self.assertEqual(list(current), ["base"])
        self.assertEqual(current["base"]["seconds_per_audio_minute"], 12.0)


class StartGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_without_downloaded_models(self) -> None:
        with patch.object(converter, "get_model_status", return_value={"phase": "ready"}), \
             patch.object(converter, "get_available_models", return_value=["base"]), \
             patch.object(converter, "is_model_cached", return_value=False):
            with self.assertRaises(ValueError):
                await benchmark.start_benchmark()

    async def test_refuses_while_model_loads_or_run_active(self) -> None:
        with patch.object(converter, "get_model_status", return_value={"phase": "loading"}):
            with self.assertRaises(RuntimeError):
                await benchmark.start_benchmark(["base"])
        with patch.object(benchmark, "get_run_state", return_value={"phase": "running"}):
            with self.assertRaises(RuntimeError):
                await benchmark.start_benchmark(["base"])

    async def test_run_measures_each_model_and_stores(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        store = benchmark.BenchmarkStore(Path(tmp.name) / "b.sqlite3")
        benchmark.set_store(store)
        try:
            fake = lambda model, device, ct: benchmark.make_result(model, device, ct, 15.0, {"base": 3.0, "small": 9.0}[model])  # noqa: E731
            with patch.object(converter, "get_model_status", return_value={"phase": "ready"}), \
                 patch.object(converter, "get_available_models", return_value=["base", "small", "medium"]), \
                 patch.object(converter, "is_model_cached", side_effect=lambda m: m in ("base", "small")), \
                 patch.object(benchmark, "_benchmark_model_sync", side_effect=fake), \
                 patch.dict("os.environ", {"HOLY_MOLY_DEVICE": "cpu", "HOLY_MOLY_COMPUTE_TYPE": "int8"}):
                models = await benchmark.start_benchmark()
                self.assertEqual(models, ["base", "small"])
                for _ in range(50):
                    await asyncio.sleep(0.05)
                    if benchmark.get_run_state()["phase"] != "running":
                        break
            state = benchmark.get_run_state()
            self.assertEqual(state["phase"], "done")
            self.assertEqual(state["done"], 2)
            self.assertEqual([r["model"] for r in state["results"]], ["base", "small"])
            self.assertEqual(store.get("small", "cpu", "int8").seconds_per_audio_minute, 36.0)
        finally:
            benchmark.set_store(None)
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
