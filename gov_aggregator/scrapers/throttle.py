"""Per-host politeness throttle — avoids hammering (and getting banned by) any
single government server during a crawl.

Each request to a given host is spaced at least ``min_interval`` seconds apart,
plus a small random jitter so traffic doesn't look robotic. Requests to
*different* hosts still run fully in parallel, so the overall crawl stays fast.

Loop-agnostic by design: the reservation critical section uses a short-lived
``threading.Lock`` (held only for a few microseconds of arithmetic), then the
actual delay is awaited with ``asyncio.sleep`` in whatever event loop is
running. This matters because the Playwright pool runs in its own event loop
inside a worker thread — an ``asyncio.Lock`` created in one loop cannot be
awaited in another.
"""
from __future__ import annotations

import asyncio
import random
import threading
import time
from urllib.parse import urlparse


class HostThrottle:
    def __init__(self, min_interval: float = 1.0, jitter: float = 0.5) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._next_allowed: dict[str, float] = {}
        self._lock = threading.Lock()

    async def wait(self, url: str) -> None:
        """Block until it's polite to hit ``url``'s host, then reserve the slot."""
        host = urlparse(url).netloc.lower()
        if not host:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_allowed.get(host, 0.0))
            interval = self.min_interval + random.uniform(0.0, self.jitter)
            # Reserve this host's next slot so concurrent callers queue up.
            self._next_allowed[host] = start + interval
            sleep_for = start - now
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)


# Shared singleton used by the generic engine and the PDF downloader.
HOST_THROTTLE = HostThrottle()
