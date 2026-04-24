"""Human-like browser interaction primitives.

The browser automation defaults shipped by nodriver fire synthetic clicks at
element centers with no preceding pointer history, type with flat uniform
delays, and scroll instantly to ``document.body.scrollHeight``. Detectors look
exactly for that. This module provides drop-in helpers that route every click,
keystroke, scroll, and pause through richer, per-session randomized models so
runs look like a real person browsing.

Design summary:
    * ``SessionRhythm`` is sampled once per session (optionally derived from an
      account-stable seed) and carries personality multipliers, the simulated
      mouse position, and pacing state.
    * Sleep distributions are log-normal (fat right tail) rather than uniform.
    * Typing models bigram speed-ups, word-boundary pauses, mid-word thinking
      pauses, occasional bursts, and rare adjacent-key typos with backspace.
    * Clicks dispatch a Bezier mouse path through CDP with jitter, optional
      overshoot/correction, hover dwell, and explicit press/release.
    * Scrolling fires CDP wheel events in chunks with backscroll and inertia.
    * Warmup, cooldown, inter-target rest, and decoy-click helpers add
      session-level camouflage.

All helpers are best-effort: if a CDP call is unsupported by the running
browser they fall back to the older nodriver primitives so the search task
still completes.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .browser import LinkedInBrowser


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_VIEWPORT_W = 1280
DEFAULT_VIEWPORT_H = 800

# Common English bigrams that get a typing speed bonus (very rough but cheap).
_FAST_BIGRAMS: frozenset[str] = frozenset(
    {
        "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd",
        "ti", "es", "or", "te", "of", "ed", "is", "it", "al", "ar",
        "st", "to", "nt", "ng", "se", "ha", "as", "ou", "io", "le",
        "ve", "co", "me", "de", "hi", "ri", "ro", "ic", "ne", "ea",
    }
)

# Adjacent keys on a US QWERTY layout, used for typo generation.
_ADJACENT_KEYS: dict[str, str] = {
    "q": "wa",  "w": "qeas", "e": "wrds", "r": "etdf", "t": "rygf",
    "y": "tugh", "u": "yihj", "i": "uojk", "o": "ipkl", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc",
    "g": "ftyhvb", "h": "gyujnb", "j": "huiknm", "k": "jiolm",
    "l": "kop", "z": "asx",  "x": "zsdc", "c": "xdfv", "v": "cfgb",
    "b": "vghn", "n": "bhjm", "m": "njk",
}


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------

# Each kind: (mean_ms, sigma) for a log-normal sample, plus optional caps.
_SLEEP_KINDS: dict[str, tuple[float, float, float, float]] = {
    # name           mean_ms  sigma  min_ms  max_ms
    "tick":          (90.0,   0.35,   30.0,    400.0),
    "micro":         (320.0,  0.45,  120.0,   1200.0),
    "think":         (1400.0, 0.55,  500.0,   6000.0),
    "scan":          (3500.0, 0.50, 1500.0,  12000.0),
    "deliberate":    (5500.0, 0.55, 2500.0,  20000.0),
    "page_load":     (4500.0, 0.40, 2500.0,  12000.0),
    "filter_apply":  (4500.0, 0.45, 3000.0,  14000.0),
    "next_page":     (5000.0, 0.40, 3000.0,  12000.0),
    "between_target":(60_000.0, 0.55, 25_000.0, 240_000.0),
}

# Probability per top-level sleep that a "micro break" stretches it 20-60s.
_MICRO_BREAK_BASE_PROB = 0.02


def _sample_lognormal(rng: random.Random, mean_ms: float, sigma: float, lo: float, hi: float) -> float:
    """Sample a log-normal value clamped to ``[lo, hi]`` milliseconds.

    ``mean_ms`` is the desired *median*, not the arithmetic mean of the
    underlying normal. ``mu = ln(mean_ms)`` makes the distribution's median
    line up with ``mean_ms`` which is what the surrounding code reasons about.
    """
    mu = math.log(max(1.0, mean_ms))
    sample = math.exp(rng.normalvariate(mu, sigma))
    return max(lo, min(hi, sample))


# ---------------------------------------------------------------------------
# SessionRhythm
# ---------------------------------------------------------------------------


@dataclass
class SessionRhythm:
    """Per-session humanization personality and pacing state.

    Holds:
        * Speed and jitter multipliers (set once, used everywhere)
        * Behavioural probabilities (typo rate, break rate, decoy rate)
        * Live mouse position (x, y) so subsequent moves start from "where the
          mouse already is" rather than teleporting
        * Pacing counters: action count + last-action timestamp + minimum gap
    """

    rng: random.Random
    speed_mult: float
    jitter_mult: float
    break_chance: float
    typo_rate: float
    decoy_rate: float
    mouse_x: float = DEFAULT_VIEWPORT_W / 2
    mouse_y: float = DEFAULT_VIEWPORT_H / 2
    last_action_ts: float = 0.0
    action_count: int = 0
    min_gap_ms: float = 280.0
    started_at: float = field(default_factory=time.time)
    hourly_action_cap: int = 220
    seed_label: str | None = None

    # ----- Constructors -------------------------------------------------

    @classmethod
    def from_seed(cls, seed: str | None) -> "SessionRhythm":
        """Build a rhythm; if ``seed`` is provided, personality is reproducible.

        Pass an account-stable string (e.g. session-file path or LinkedIn
        account hint) to keep the same "user" feeling consistent across runs.
        """
        if seed is None:
            rng = random.Random()
            label = None
        else:
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            rng = random.Random(int(digest[:16], 16))
            label = seed

        speed_mult = rng.uniform(0.85, 1.25)
        jitter_mult = rng.uniform(0.8, 1.3)
        break_chance = rng.uniform(0.015, 0.035)
        typo_rate = rng.uniform(0.012, 0.035)
        decoy_rate = rng.uniform(0.04, 0.09)
        min_gap_ms = rng.uniform(220.0, 360.0)

        return cls(
            rng=rng,
            speed_mult=speed_mult,
            jitter_mult=jitter_mult,
            break_chance=break_chance,
            typo_rate=typo_rate,
            decoy_rate=decoy_rate,
            min_gap_ms=min_gap_ms,
            seed_label=label,
        )

    # ----- Pacing -------------------------------------------------------

    async def gate_min_gap(self) -> None:
        """Enforce a minimum delay since the last user-visible action."""
        now = time.monotonic()
        elapsed_ms = (now - self.last_action_ts) * 1000.0 if self.last_action_ts else float("inf")
        deficit = self.min_gap_ms - elapsed_ms
        if deficit > 0:
            await asyncio.sleep(deficit / 1000.0)
        self.last_action_ts = time.monotonic()
        self.action_count += 1

    def hourly_pace_pause_seconds(self) -> float:
        """If we've consumed our hourly action budget, return seconds to rest.

        Returns 0 when no break is required.
        """
        elapsed = max(1.0, time.time() - self.started_at)
        rate_per_hour = self.action_count / (elapsed / 3600.0)
        if rate_per_hour <= self.hourly_action_cap:
            return 0.0
        # Force a break long enough to drop average rate back under the cap.
        target = self.action_count / self.hourly_action_cap * 3600.0
        return max(0.0, target - elapsed)


# ---------------------------------------------------------------------------
# Sleep helpers
# ---------------------------------------------------------------------------


async def human_sleep(
    rhythm: SessionRhythm,
    kind: str = "think",
    *,
    multiplier: float = 1.0,
) -> float:
    """Sleep for a humanized duration. Returns seconds actually slept."""
    if kind not in _SLEEP_KINDS:
        kind = "think"
    mean, sigma, lo, hi = _SLEEP_KINDS[kind]
    base_ms = _sample_lognormal(rhythm.rng, mean, sigma, lo, hi)

    if kind not in {"between_target", "tick"} and rhythm.rng.random() < rhythm.break_chance:
        # Distraction: somebody glanced at Slack.
        base_ms += _sample_lognormal(rhythm.rng, 25_000.0, 0.5, 8_000.0, 90_000.0)

    base_ms *= multiplier
    base_ms /= rhythm.speed_mult  # faster personality => shorter waits
    seconds = base_ms / 1000.0
    await asyncio.sleep(seconds)
    return seconds


async def maybe_micro_break(rhythm: SessionRhythm) -> None:
    """Occasionally insert a "looked away" pause unrelated to the next step."""
    if rhythm.rng.random() < _MICRO_BREAK_BASE_PROB:
        ms = _sample_lognormal(rhythm.rng, 18_000.0, 0.6, 6_000.0, 90_000.0)
        await asyncio.sleep(ms / 1000.0)


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------


def _per_char_delay_ms(rhythm: SessionRhythm, prev: str | None, ch: str) -> float:
    base = _sample_lognormal(rhythm.rng, 95.0, 0.45, 18.0, 480.0)
    if prev is not None:
        bigram = (prev + ch).lower()
        if bigram in _FAST_BIGRAMS:
            base *= 0.55
    if ch == " ":
        base += _sample_lognormal(rhythm.rng, 130.0, 0.45, 40.0, 600.0)
    if not ch.isalpha() and ch not in {" ", "-"}:
        # Numbers and punctuation tend to be slower.
        base *= 1.2
    base /= rhythm.speed_mult
    return base


def _typo_for(rhythm: SessionRhythm, ch: str) -> str | None:
    lower = ch.lower()
    options = _ADJACENT_KEYS.get(lower)
    if not options:
        return None
    pick = rhythm.rng.choice(options)
    return pick.upper() if ch.isupper() else pick


async def human_type(
    browser: LinkedInBrowser,
    element: Any,
    text: str,
    rhythm: SessionRhythm,
    *,
    allow_typos: bool = True,
    allow_thinking_pauses: bool = True,
) -> None:
    """Type ``text`` into ``element`` with a humanized rhythm.

    The caller is expected to have focused the input first (clicked it).
    """
    if not text:
        return

    prev: str | None = None
    burst_remaining = 0
    tokens: list[tuple[str, str]] = []  # (char, kind) where kind in {"send","backspace"}
    for ch in text:
        tokens.append((ch, "send"))

    i = 0
    while i < len(tokens):
        ch, kind = tokens[i]
        if kind == "send":
            # Mid-string thinking pause occasionally.
            if (
                allow_thinking_pauses
                and prev not in (None, " ")
                and rhythm.rng.random() < 0.04
                and len(text) > 6
            ):
                await asyncio.sleep(_sample_lognormal(rhythm.rng, 700.0, 0.45, 250.0, 2200.0) / 1000.0)

            # Decide: typo this char?
            do_typo = (
                allow_typos
                and ch.isalpha()
                and len(text) >= 5
                and rhythm.rng.random() < rhythm.typo_rate
            )
            if do_typo:
                wrong = _typo_for(rhythm, ch)
                if wrong is not None:
                    await element.send_keys(wrong)
                    await asyncio.sleep(_per_char_delay_ms(rhythm, prev, wrong) / 1000.0)
                    # Notice + correct.
                    await asyncio.sleep(_sample_lognormal(rhythm.rng, 240.0, 0.4, 80.0, 900.0) / 1000.0)
                    await browser.press_key("Backspace", "Backspace", 8)
                    await asyncio.sleep(_sample_lognormal(rhythm.rng, 130.0, 0.35, 50.0, 500.0) / 1000.0)

            await element.send_keys(ch)
            await asyncio.sleep(_per_char_delay_ms(rhythm, prev, ch) / 1000.0)
            prev = ch

            # Burst? If yes, the next 1-3 chars get fired faster (post-delay halved).
            if burst_remaining == 0 and rhythm.rng.random() < 0.12 and ch != " ":
                burst_remaining = rhythm.rng.randint(1, 3)
            elif burst_remaining > 0:
                burst_remaining -= 1
                await asyncio.sleep(min(0.04, _per_char_delay_ms(rhythm, prev, ch) / 1000.0 * 0.4))
        i += 1


# ---------------------------------------------------------------------------
# Mouse choreography
# ---------------------------------------------------------------------------


_BUTTON_MAP_CACHE: dict[str, Any] | None = None


def _resolve_mouse_button(browser: LinkedInBrowser, name: str) -> Any:
    """Resolve string button name to nodriver's MouseButton enum value."""
    global _BUTTON_MAP_CACHE
    if _BUTTON_MAP_CACHE is None:
        try:
            from nodriver.cdp.input_ import MouseButton
            _BUTTON_MAP_CACHE = {
                "none": MouseButton.NONE,
                "left": MouseButton.LEFT,
                "middle": MouseButton.MIDDLE,
                "right": MouseButton.RIGHT,
            }
        except Exception:
            _BUTTON_MAP_CACHE = {}
    return _BUTTON_MAP_CACHE.get(name, _BUTTON_MAP_CACHE.get("none", name))


async def _dispatch_mouse(
    browser: LinkedInBrowser,
    *,
    type_: str,
    x: float,
    y: float,
    button: str = "none",
    buttons: int = 0,
    click_count: int = 0,
    delta_x: float = 0.0,
    delta_y: float = 0.0,
) -> bool:
    """Best-effort CDP mouse dispatch; returns False if unsupported."""
    if not getattr(browser, "_cdp_input", None):
        return False
    try:
        cdp_input = browser._cdp_input
        cmd_kwargs: dict[str, Any] = dict(
            type_=type_,
            x=float(x),
            y=float(y),
            button=_resolve_mouse_button(browser, button),
            buttons=int(buttons),
            click_count=int(click_count),
            modifiers=0,
        )
        if type_ == "mouseWheel":
            cmd_kwargs["delta_x"] = float(delta_x)
            cmd_kwargs["delta_y"] = float(delta_y)
        await browser.tab.send(cdp_input.dispatch_mouse_event(**cmd_kwargs))
        return True
    except Exception:
        return False


def _bezier_point(t: float, p0: tuple[float, float], p1: tuple[float, float],
                  p2: tuple[float, float], p3: tuple[float, float]) -> tuple[float, float]:
    one = 1.0 - t
    x = (one ** 3) * p0[0] + 3 * (one ** 2) * t * p1[0] + 3 * one * (t ** 2) * p2[0] + (t ** 3) * p3[0]
    y = (one ** 3) * p0[1] + 3 * (one ** 2) * t * p1[1] + 3 * one * (t ** 2) * p2[1] + (t ** 3) * p3[1]
    return x, y


def _ease_out_samples(rng: random.Random, n: int) -> list[float]:
    """Return ``n`` t-values in [0,1] for position sampling.

    Values follow a cubic ease-out so the *position* moves quickly at the
    start of the path and slows toward the target (the natural shape of human
    ballistic mouse movement). Per-step time spacing is handled by the caller
    and should remain uniform.
    """
    out: list[float] = []
    for i in range(1, n + 1):
        u = i / n
        eased = 1.0 - (1.0 - u) ** 3
        # Tiny noise on interior samples; never push past 1.0.
        if i < n:
            eased += rng.uniform(-0.015, 0.015)
        out.append(max(0.001, min(1.0, eased)))
    # Deduplicate to avoid emitting redundant identical mouse-move events.
    seen: set[float] = set()
    unique: list[float] = []
    for v in out:
        rounded = round(v, 4)
        if rounded in seen:
            continue
        seen.add(rounded)
        unique.append(v)
    unique.sort()
    return unique


async def _move_mouse_to(
    browser: LinkedInBrowser,
    rhythm: SessionRhythm,
    target_x: float,
    target_y: float,
    *,
    overshoot: bool | None = None,
) -> None:
    start = (rhythm.mouse_x, rhythm.mouse_y)
    end = (target_x, target_y)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance < 1.0:
        return

    # Two control points around the chord, jittered perpendicular for arc.
    perp_x, perp_y = -dy / max(distance, 1.0), dx / max(distance, 1.0)
    arc_strength = rhythm.rng.uniform(0.05, 0.25) * distance * rhythm.jitter_mult
    sign = 1.0 if rhythm.rng.random() < 0.5 else -1.0

    p1 = (
        start[0] + dx * 0.33 + perp_x * arc_strength * sign,
        start[1] + dy * 0.33 + perp_y * arc_strength * sign,
    )
    p2 = (
        start[0] + dx * 0.66 + perp_x * arc_strength * sign * 0.7,
        start[1] + dy * 0.66 + perp_y * arc_strength * sign * 0.7,
    )
    p3 = end

    if overshoot is None:
        overshoot = rhythm.rng.random() < 0.10 and distance > 60
    if overshoot:
        # Overshoot along the same direction by 8-22 px, then correct back.
        ox = end[0] + (dx / distance) * rhythm.rng.uniform(8.0, 22.0)
        oy = end[1] + (dy / distance) * rhythm.rng.uniform(8.0, 22.0)
        await _move_mouse_to(browser, rhythm, ox, oy, overshoot=False)
        # tiny correction pause
        await asyncio.sleep(_sample_lognormal(rhythm.rng, 80.0, 0.4, 30.0, 250.0) / 1000.0)

    # Number of samples scales with distance (Fitts' law-ish).
    steps = max(8, min(40, int(distance / 25)))
    ts = _ease_out_samples(rhythm.rng, steps)
    total_ms = max(120.0, min(650.0, 90 + distance * 0.9)) / rhythm.speed_mult
    # Each sample spaced uniformly in time; the eased position values produce
    # the visual sense of deceleration without front-loading the wall clock.
    per_step_ms = total_ms / max(1, len(ts))

    for t in ts:
        x, y = _bezier_point(t, start, p1, p2, p3)
        x += rhythm.rng.uniform(-0.6, 0.6)
        y += rhythm.rng.uniform(-0.6, 0.6)
        await _dispatch_mouse(browser, type_="mouseMoved", x=x, y=y)
        rhythm.mouse_x, rhythm.mouse_y = x, y
        # +/- 25% jitter on each step's wait so the cadence isn't perfectly metronomic.
        jittered = per_step_ms * rhythm.rng.uniform(0.75, 1.25)
        await asyncio.sleep(max(0.005, jittered / 1000.0))


async def _element_target_point(browser: LinkedInBrowser, element: Any,
                                rhythm: SessionRhythm) -> tuple[float, float] | None:
    """Return a randomized click point inside ``element``'s viewport box."""
    try:
        box = await element.apply(
            """
            el => {
              const r = el.getBoundingClientRect();
              if (r.width <= 0 || r.height <= 0) return null;
              return { x: r.left, y: r.top, w: r.width, h: r.height };
            }
            """
        )
    except Exception:
        return None
    if not box or not isinstance(box, dict):
        return None
    width = float(box.get("w", 0))
    height = float(box.get("h", 0))
    if width <= 0 or height <= 0:
        return None

    # Aim for the center, but pull off-center with Gaussian jitter capped at the box.
    cx = float(box.get("x", 0)) + width / 2
    cy = float(box.get("y", 0)) + height / 2
    jitter_x = rhythm.rng.gauss(0.0, width / 6.0) * rhythm.jitter_mult
    jitter_y = rhythm.rng.gauss(0.0, height / 6.0) * rhythm.jitter_mult
    x = max(box["x"] + 2, min(box["x"] + width - 2, cx + jitter_x))
    y = max(box["y"] + 2, min(box["y"] + height - 2, cy + jitter_y))
    return x, y


async def human_click(
    browser: LinkedInBrowser,
    element: Any,
    rhythm: SessionRhythm,
    *,
    pre_hover_ms: tuple[float, float] = (60.0, 220.0),
    button: str = "left",
) -> None:
    """Move the simulated mouse to ``element`` and click it via CDP.

    Falls back to ``element.click()`` if any CDP step fails or the box can't
    be read (e.g. element off-screen or detached).
    """
    await rhythm.gate_min_gap()

    try:
        await element.scroll_into_view()
    except Exception:
        pass

    target = await _element_target_point(browser, element, rhythm)
    if target is None:
        # No viewport box; degrade gracefully.
        try:
            await element.click()
        except Exception:
            raise
        return

    x, y = target
    await _move_mouse_to(browser, rhythm, x, y)

    hover_ms = rhythm.rng.uniform(*pre_hover_ms) / rhythm.speed_mult
    await asyncio.sleep(hover_ms / 1000.0)

    pressed = await _dispatch_mouse(
        browser, type_="mousePressed", x=x, y=y,
        button=button, buttons=1 if button == "left" else 4 if button == "middle" else 2,
        click_count=1,
    )
    if not pressed:
        try:
            await element.click()
        except Exception:
            raise
        return

    await asyncio.sleep(_sample_lognormal(rhythm.rng, 75.0, 0.35, 30.0, 250.0) / 1000.0)

    await _dispatch_mouse(
        browser, type_="mouseReleased", x=x, y=y,
        button=button, buttons=0, click_count=1,
    )


async def hover_drift(browser: LinkedInBrowser, rhythm: SessionRhythm,
                      around_selector: str | None = None) -> None:
    """Tiny idle mouse motion. Cheap camouflage during long dwells."""
    if rhythm.rng.random() > 0.35:
        return
    target_x = rhythm.mouse_x + rhythm.rng.uniform(-25.0, 25.0)
    target_y = rhythm.mouse_y + rhythm.rng.uniform(-15.0, 15.0)
    target_x = max(20.0, min(DEFAULT_VIEWPORT_W - 20.0, target_x))
    target_y = max(20.0, min(DEFAULT_VIEWPORT_H - 20.0, target_y))
    await _move_mouse_to(browser, rhythm, target_x, target_y, overshoot=False)


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------


async def human_scroll(
    browser: LinkedInBrowser,
    rhythm: SessionRhythm,
    *,
    direction: str = "down",
    pixels: int | None = None,
    until_bottom: bool = False,
    max_ticks: int = 60,
) -> None:
    """Scroll the page using CDP wheel events with chunked, jittered deltas.

    Either ``pixels`` (approximate total) or ``until_bottom=True`` should be
    set. The scroll position lives at the simulated mouse coordinates so the
    page receives realistic wheel events at a believable origin.
    """
    if pixels is None and not until_bottom:
        pixels = 600

    sign = 1.0 if direction == "down" else -1.0
    remaining = pixels if pixels is not None else 10_000
    ticks = 0
    no_progress_ticks = 0
    last_scroll_y: float | None = None

    while ticks < max_ticks:
        chunk = rhythm.rng.uniform(120.0, 480.0) * rhythm.jitter_mult
        dy = chunk * sign

        moved = await _dispatch_mouse(
            browser, type_="mouseWheel",
            x=rhythm.mouse_x, y=rhythm.mouse_y,
            delta_x=0.0, delta_y=dy,
        )
        if not moved:
            # Fall back to JS scrollBy for environments without CDP wheel.
            try:
                await browser.evaluate(f"window.scrollBy(0, {int(dy)})")
            except Exception:
                break

        await asyncio.sleep(_sample_lognormal(rhythm.rng, 220.0, 0.45, 80.0, 700.0) / 1000.0)

        # Occasional small backscroll (re-reading).
        if rhythm.rng.random() < 0.06 and direction == "down":
            back = rhythm.rng.uniform(60.0, 200.0)
            await _dispatch_mouse(
                browser, type_="mouseWheel",
                x=rhythm.mouse_x, y=rhythm.mouse_y,
                delta_x=0.0, delta_y=-back,
            )
            await asyncio.sleep(_sample_lognormal(rhythm.rng, 320.0, 0.4, 150.0, 900.0) / 1000.0)

        ticks += 1
        if pixels is not None:
            remaining -= chunk
            if remaining <= 0:
                break

        if until_bottom:
            try:
                pos = await browser.evaluate(
                    "JSON.stringify({y: window.scrollY, h: document.body.scrollHeight, ih: window.innerHeight})"
                )
                if isinstance(pos, str):
                    import json as _json
                    data = _json.loads(pos)
                    y = float(data.get("y", 0))
                    h = float(data.get("h", 0))
                    ih = float(data.get("ih", 0))
                    if y + ih >= h - 10:
                        break
                    if last_scroll_y is not None and abs(y - last_scroll_y) < 5:
                        no_progress_ticks += 1
                        if no_progress_ticks >= 3:
                            break
                    else:
                        no_progress_ticks = 0
                    last_scroll_y = y
            except Exception:
                pass

    # Inertia tail.
    for _ in range(rhythm.rng.randint(1, 3)):
        small = rhythm.rng.uniform(20.0, 90.0) * sign
        await _dispatch_mouse(
            browser, type_="mouseWheel",
            x=rhythm.mouse_x, y=rhythm.mouse_y,
            delta_x=0.0, delta_y=small,
        )
        await asyncio.sleep(_sample_lognormal(rhythm.rng, 180.0, 0.3, 70.0, 400.0) / 1000.0)


# ---------------------------------------------------------------------------
# Session arcs: warmup / cooldown / inter-target rest
# ---------------------------------------------------------------------------


async def session_warmup(browser: LinkedInBrowser, rhythm: SessionRhythm) -> None:
    """Visit feed (and optionally notifications) and scroll briefly.

    No-op on errors: warmup is camouflage, never a critical path.
    """
    try:
        await browser.goto("https://www.linkedin.com/feed", wait_seconds=0.0)
        await human_sleep(rhythm, "page_load")
        await human_scroll(browser, rhythm, pixels=int(rhythm.rng.uniform(700, 1600)))
        await human_sleep(rhythm, "scan")
        if rhythm.rng.random() < 0.4:
            await human_scroll(browser, rhythm, pixels=int(rhythm.rng.uniform(400, 1000)))
            await human_sleep(rhythm, "scan")
        if rhythm.rng.random() < 0.25:
            await browser.goto("https://www.linkedin.com/notifications/", wait_seconds=0.0)
            await human_sleep(rhythm, "page_load")
            await human_scroll(browser, rhythm, pixels=int(rhythm.rng.uniform(300, 900)))
            await human_sleep(rhythm, "scan")
    except Exception:
        return


async def session_cooldown(browser: LinkedInBrowser, rhythm: SessionRhythm) -> None:
    """Drop to the feed and scroll a bit before the session ends."""
    try:
        await browser.goto("https://www.linkedin.com/feed", wait_seconds=0.0)
        await human_sleep(rhythm, "page_load")
        await human_scroll(browser, rhythm, pixels=int(rhythm.rng.uniform(400, 1200)))
        await human_sleep(rhythm, "scan")
    except Exception:
        return


async def inter_target_rest(rhythm: SessionRhythm) -> None:
    """Sleep between consecutive targets in a batch."""
    base_seconds = _sample_lognormal(
        rhythm.rng, *_SLEEP_KINDS["between_target"][:2],
        _SLEEP_KINDS["between_target"][2], _SLEEP_KINDS["between_target"][3],
    ) / 1000.0
    base_seconds /= rhythm.speed_mult
    if rhythm.rng.random() < 0.10:
        # Occasional long break: 3-8 minutes.
        base_seconds += rhythm.rng.uniform(180.0, 480.0)
    await asyncio.sleep(base_seconds)


# ---------------------------------------------------------------------------
# Camouflage clicks
# ---------------------------------------------------------------------------


async def maybe_decoy_profile_open(
    browser: LinkedInBrowser,
    rhythm: SessionRhythm,
    candidate_links: Iterable[Any],
) -> None:
    """With probability ``rhythm.decoy_rate``, open a profile in a new tab,
    dwell briefly, then close it. Pure camouflage; no extraction.

    ``candidate_links`` is an iterable of nodriver elements pointing at
    ``/in/`` profiles already on the page. The caller can pass an empty
    iterable safely.
    """
    candidates = [el for el in candidate_links]
    if not candidates:
        return
    if rhythm.rng.random() > rhythm.decoy_rate:
        return

    target = rhythm.rng.choice(candidates)
    try:
        href = await target.apply("el => el.href || el.getAttribute('href') || ''")
    except Exception:
        return
    if not isinstance(href, str) or "/in/" not in href:
        return
    if href.startswith("/"):
        href = "https://www.linkedin.com" + href

    new_tab = None
    try:
        new_tab = await browser.browser.get(href, new_tab=True)
        await asyncio.sleep(_sample_lognormal(rhythm.rng, 3500.0, 0.4, 1500.0, 9000.0) / 1000.0)
    except Exception:
        return
    finally:
        if new_tab is not None:
            try:
                await new_tab.close()
            except Exception:
                pass
