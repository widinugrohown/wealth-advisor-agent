"""Failure injection for the mock CRM.

Two ways to use it:
  * env CRM_CHAOS=0.3     - ~30% of calls misbehave, reproducible via CRM_CHAOS_SEED
  * header X-Chaos: timeout|http_500|missing_field  - one-off, for a single curl

The RNG is created once per process, not per request: re-seeding on every call
made the sequence identical for every request, so a "30% failure rate" silently
became 0% (first draw from Random(7) is 0.32). Measured, then fixed.
"""
from __future__ import annotations

import os
import random
import time

CHAOS_MODES = ("timeout", "http_500", "missing_field")
DEFAULT_SEED = 7

_rand = random.Random(int(os.getenv("CRM_CHAOS_SEED", DEFAULT_SEED)))


def chaos_rate() -> float:
    return float(os.getenv("CRM_CHAOS", "0.0") or 0.0)


def pick_mode() -> str | None:
    """Return a failure mode for this request, or None for a healthy response."""
    rate = chaos_rate()
    if rate <= 0:
        return None
    if _rand.random() >= rate:
        return None
    return _rand.choice(CHAOS_MODES)


def apply_failure(mode: str) -> None:
    """Simulate the failure server-side. 'timeout' sleeps past the client timeout."""
    if mode == "timeout":
        time.sleep(float(os.getenv("CRM_FAKE_DELAY_S", "5")))
