"""Unit tests for humanization primitives."""

from __future__ import annotations

import asyncio
import statistics
import unittest

from linkedin_search.humanize import (
    SessionRhythm,
    _per_char_delay_ms,
    _sample_lognormal,
    _dispatch_mouse,
    human_sleep,
)


class SessionRhythmTest(unittest.TestCase):
    def test_seeded_personality_is_reproducible(self) -> None:
        a = SessionRhythm.from_seed("session/path/account-A.json")
        b = SessionRhythm.from_seed("session/path/account-A.json")
        self.assertAlmostEqual(a.speed_mult, b.speed_mult)
        self.assertAlmostEqual(a.jitter_mult, b.jitter_mult)
        self.assertAlmostEqual(a.typo_rate, b.typo_rate)
        self.assertAlmostEqual(a.decoy_rate, b.decoy_rate)
        self.assertAlmostEqual(a.min_gap_ms, b.min_gap_ms)

    def test_different_seeds_produce_different_personalities(self) -> None:
        a = SessionRhythm.from_seed("seed-A")
        b = SessionRhythm.from_seed("seed-B")
        self.assertNotAlmostEqual(a.speed_mult, b.speed_mult)

    def test_unseeded_rhythm_is_usable(self) -> None:
        r = SessionRhythm.from_seed(None)
        self.assertGreater(r.speed_mult, 0.0)
        self.assertGreater(r.min_gap_ms, 0.0)
        self.assertIsNone(r.seed_label)

    def test_personality_bounds(self) -> None:
        for seed in ["a", "b", "c", "d", "e", "f", "g", "h"]:
            r = SessionRhythm.from_seed(seed)
            self.assertGreaterEqual(r.speed_mult, 0.85)
            self.assertLessEqual(r.speed_mult, 1.25)
            self.assertGreaterEqual(r.typo_rate, 0.012)
            self.assertLessEqual(r.typo_rate, 0.035)


class LogNormalTest(unittest.TestCase):
    def test_lognormal_respects_bounds(self) -> None:
        r = SessionRhythm.from_seed("lognormal-test")
        samples = [_sample_lognormal(r.rng, 1000.0, 0.5, 200.0, 5000.0) for _ in range(5000)]
        self.assertGreaterEqual(min(samples), 200.0)
        self.assertLessEqual(max(samples), 5000.0)
        # Median should be near the requested mean since we exponentiate ln(mean).
        self.assertLess(abs(statistics.median(samples) - 1000.0), 200.0)


class HumanSleepTest(unittest.TestCase):
    def test_human_sleep_returns_positive_duration(self) -> None:
        async def _run() -> float:
            r = SessionRhythm.from_seed("sleep-test")
            # Eliminate per-personality drift by forcing speed_mult to 1.
            r.speed_mult = 1.0
            r.break_chance = 0.0
            return await human_sleep(r, "tick")

        slept = asyncio.run(_run())
        self.assertGreaterEqual(slept, 0.030)
        self.assertLessEqual(slept, 0.500)

    def test_unknown_sleep_kind_falls_back_to_think(self) -> None:
        async def _run() -> float:
            r = SessionRhythm.from_seed("fallback")
            r.speed_mult = 1.0
            r.break_chance = 0.0
            return await human_sleep(r, "this-kind-does-not-exist")

        slept = asyncio.run(_run())
        # 'think' kind is bounded at 6s upper.
        self.assertGreaterEqual(slept, 0.5)
        self.assertLessEqual(slept, 6.5)


class TypingDelayTest(unittest.TestCase):
    def test_common_bigram_is_faster_on_average(self) -> None:
        r = SessionRhythm.from_seed("typing-bigram")
        r.speed_mult = 1.0

        # Sample many delays for a fast bigram and an unusual one.
        fast = statistics.median(_per_char_delay_ms(r, "t", "h") for _ in range(2000))
        slow = statistics.median(_per_char_delay_ms(r, "z", "q") for _ in range(2000))
        self.assertLess(fast, slow)

    def test_space_pause_is_added(self) -> None:
        r = SessionRhythm.from_seed("typing-space")
        r.speed_mult = 1.0
        word_char = statistics.median(_per_char_delay_ms(r, "a", "n") for _ in range(2000))
        space_char = statistics.median(_per_char_delay_ms(r, "n", " ") for _ in range(2000))
        self.assertGreater(space_char, word_char)


class MouseDispatchFallbackTest(unittest.TestCase):
    def test_dispatch_returns_false_when_cdp_unavailable(self) -> None:
        class _NoCdpBrowser:
            _cdp_input = None
            tab = None

        async def _run() -> bool:
            return await _dispatch_mouse(_NoCdpBrowser(), type_="mouseMoved", x=100, y=100)

        self.assertFalse(asyncio.run(_run()))


class GateMinGapTest(unittest.TestCase):
    def test_min_gap_increments_action_count(self) -> None:
        async def _run() -> int:
            r = SessionRhythm.from_seed("gap")
            r.min_gap_ms = 5.0  # keep test fast
            await r.gate_min_gap()
            await r.gate_min_gap()
            return r.action_count

        self.assertEqual(asyncio.run(_run()), 2)


if __name__ == "__main__":
    unittest.main()
