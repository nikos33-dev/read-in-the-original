#!/usr/bin/env python3
"""Thin retry/backoff wrapper around oai.py's chat_json.

At Divine-Comedy scale (~2,000 calls) transient failures — 429 rate limits, 5xx, curl
timeouts — are expected. This retries with linear backoff so one blip doesn't drop a unit;
anything still failing after the retries is left for the resumable re-run to pick up.
"""
from __future__ import annotations

import threading
import time

import oai

# Default model. Deliberately the cheap mini tier so a run with no explicit --model never
# accidentally fires a large job on a pricier flagship. Override per-call with --model.
DEFAULT_MODEL = "gpt-5-mini"

# Cost metering: every call's token usage is accumulated here (thread-safe).
_LOCK = threading.Lock()
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

# Permanent failures (no point retrying, and once seen the whole run should abort fast).
_STOP = threading.Event()
_PERMANENT = ("insufficient_quota", "exceeded your current quota", "billing",
              "invalid_api_key", "incorrect api key", "account is not active")
# Rate-limit signatures -> back off concurrency (transient; NOT the permanent quota error above).
_RATE_LIMIT = ("rate limit", "rate_limit", "ratelimit", "429", "too many requests")


def _is_permanent(msg: str) -> bool:
    m = msg.lower()
    return any(p in m for p in _PERMANENT)


def _is_rate_limit(msg: str) -> bool:
    m = msg.lower()
    return any(p in m for p in _RATE_LIMIT)


class _AdaptiveLimiter:
    """Gate on concurrent in-flight LLM calls that self-tunes to the account's rate limit.

    AIMD, like TCP congestion control: start optimistic at the ceiling, HALVE on any 429
    (multiplicative decrease), and creep back up by 1 after a streak of clean successes
    (additive increase). Worker threads can outnumber the live limit — extra threads simply
    park here until a permit frees, so one fixed thread pool serves both high- and low-limit
    accounts. Capacity is adjustable at runtime (a plain Semaphore is fixed-size), so we use a
    Condition + counters.
    """

    def __init__(self, ceiling: int = 8):
        self._cv = threading.Condition()
        self.ceiling = ceiling
        self.floor = 1
        self.limit = ceiling          # current max concurrent
        self._inflight = 0
        self._ok_streak = 0           # clean successes since the last decrease

    def set_ceiling(self, ceiling: int):
        with self._cv:
            ceiling = max(1, int(ceiling))
            first = self._inflight == 0 and self._ok_streak == 0 and self.limit == self.ceiling
            self.ceiling = ceiling
            # First configure of a run: start optimistic at the ceiling. Later stages keep the
            # already-adapted limit (don't re-flood a low-limit account), just clamp to ceiling.
            self.limit = ceiling if first else min(self.limit, ceiling)
            self._cv.notify_all()

    def acquire(self):
        with self._cv:
            while self._inflight >= self.limit:
                self._cv.wait()
            self._inflight += 1

    def release(self):
        with self._cv:
            self._inflight -= 1
            self._cv.notify()

    def on_rate_limit(self):
        with self._cv:
            self.limit = max(self.floor, self.limit // 2)
            self._ok_streak = 0       # don't notify: we just shrank capacity

    def on_success(self):
        with self._cv:
            self._ok_streak += 1
            # additive increase: one extra permit after ~2 clean calls per current permit
            if self.limit < self.ceiling and self._ok_streak >= self.limit * 2:
                self.limit += 1
                self._ok_streak = 0
                self._cv.notify()


_LIMITER = _AdaptiveLimiter()


def configure_concurrency(max_concurrency: int):
    """Set the upper bound on concurrent LLM calls for this run (the AIMD ceiling)."""
    _LIMITER.set_ceiling(max_concurrency)


def concurrency_state() -> dict:
    return {"limit": _LIMITER.limit, "ceiling": _LIMITER.ceiling, "inflight": _LIMITER._inflight}


def reset_usage():
    _STOP.clear()
    with _LOCK:
        for k in USAGE:
            USAGE[k] = 0


def usage_totals() -> dict:
    with _LOCK:
        return dict(USAGE)


def chat_json(system: str, user: str, *, model: str | None = None, timeout: int = 150,
              retries: int = 3, backoff: float = 3.0) -> dict:
    if _STOP.is_set():
        raise RuntimeError("aborted: a permanent API error (quota/auth) occurred earlier")
    model = model or DEFAULT_MODEL
    last = None
    for attempt in range(retries):
        _LIMITER.acquire()                           # park here if at the live concurrency limit
        try:
            sink = []
            out = oai.chat_json(system, user, model=model, timeout=timeout, usage_sink=sink)
            if sink:
                u = sink[0]
                with _LOCK:
                    USAGE["calls"] += 1
                    USAGE["prompt_tokens"] += u.get("prompt_tokens", 0)
                    USAGE["completion_tokens"] += u.get("completion_tokens", 0)
                    USAGE["total_tokens"] += u.get("total_tokens", 0)
            _LIMITER.on_success()
            return out
        except Exception as e:                       # noqa: BLE001
            last = e
            if _is_permanent(str(e)):                # quota/auth — don't retry; abort the run
                _STOP.set()
                raise
            if _is_rate_limit(str(e)):               # 429 — shrink concurrency, then back off
                _LIMITER.on_rate_limit()
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
        finally:
            _LIMITER.release()
    raise last
