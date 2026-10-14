"""Server-side limits on POST /api/ingest/events, so one broken or hostile device cannot flood the store.
Checked BEFORE anything is written, for the whole batch (all-or-nothing, so a retry is clean; the store is idempotent anyway).
  413 body larger than ingest.max_body_bytes, or more than ingest.max_batch_events events, or one device alone sends more than the rate window allows
  411 no Content-Length (the size cannot be checked)
  403 ingest.devices is a non-empty list and an event names another device_id
  429 a device already sent ingest.max_events_per_window events in the last ingest.window_s seconds (Retry-After says when to try again)
The rate counters live in memory of the API process (reset on restart); that is documented in docs/INGESTION.md."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

DEFAULTS = {"max_body_bytes": 4_000_000, "max_batch_events": 500, "max_events_per_window": 600, "window_s": 60, "devices": []}


class LimitError(Exception):
    def __init__(self, status: int, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status, self.message, self.retry_after = status, message, retry_after


class IngestGuard:
    def __init__(self, cfg: Optional[dict] = None, clock=None):
        c = {**DEFAULTS, **((cfg or {}).get("ingest") or {})}
        self.max_body, self.max_batch = int(c["max_body_bytes"]), int(c["max_batch_events"])
        self.max_rate, self.window = int(c["max_events_per_window"]), float(c["window_s"])
        self.devices = set(c.get("devices") or [])
        self.clock = clock or time.monotonic
        self.sent: dict[str, deque] = defaultdict(deque)       # device_id -> deque of (time, n events)

    def check_size(self, content_length: Optional[str]) -> None:
        if content_length is None:
            raise LimitError(411, "Content-Length is required (the request size cannot be checked without it)")
        try:
            n = int(content_length)
        except ValueError:
            raise LimitError(400, "Content-Length is not a number") from None
        if n > self.max_body:
            raise LimitError(413, f"request body is {n} bytes; the limit is {self.max_body} bytes: send smaller batches")

    def _used(self, dev: str, now: float) -> int:
        q = self.sent[dev]
        while q and now - q[0][0] >= self.window:
            q.popleft()
        return sum(n for _, n in q)

    def check_batch(self, events) -> None:
        """events: objects with .device_id. Raises LimitError; otherwise records the batch against each device's rate window."""
        if len(events) > self.max_batch:
            raise LimitError(413, f"{len(events)} events in one batch; the limit is {self.max_batch}: send smaller batches")
        if self.devices:
            bad = sorted({e.device_id for e in events} - self.devices)
            if bad:
                raise LimitError(403, f"device not registered: {', '.join(bad[:5])}{' ...' if len(bad) > 5 else ''} (ingest.devices lists the allowed device ids)")
        per: dict[str, int] = defaultdict(int)
        for e in events:
            per[e.device_id] += 1
        now = self.clock()
        for dev, n in per.items():
            if n > self.max_rate:
                raise LimitError(413, f"device {dev}: {n} events in one batch exceeds the {self.max_rate} events per {self.window:g} s allowed; send smaller batches")
            used = self._used(dev, now)
            if used + n > self.max_rate:
                q, free, wait = self.sent[dev], self.max_rate - used, 0.0
                for t, k in q:                                   # oldest first: wait until enough of them leave the window
                    free += k
                    wait = t + self.window - now
                    if free >= n:
                        break
                raise LimitError(429, f"device {dev}: rate limit of {self.max_rate} events per {self.window:g} s reached; retry in {max(1, int(wait) + 1)} s",
                                 retry_after=max(1, int(wait) + 1))
        for dev, n in per.items():
            self.sent[dev].append((now, n))
