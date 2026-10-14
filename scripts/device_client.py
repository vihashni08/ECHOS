"""Reference client for POST /api/ingest/events (see docs/INGESTION.md). Standard library only.
Every event is first appended to an offline buffer file (JSON lines), then the buffer is sent in batches, so nothing is lost when the network or the server is down:
the next run (or the next flush) sends what is left. The server is idempotent, so re-sending after an unclear failure is safe.
Retry rules: network error / 5xx -> backoff 1, 2, 4 ... capped at 60 s, then stop and keep the buffer; 429 -> wait Retry-After; 413 -> halve the batch (one event too large -> rejected file);
other 4xx (403 token, 422 bad event, ...) -> the batch is NOT retried: bad events go to <buffer>.rejected, 403 stops the run (fix the token / device registration).
The token is read from the environment (ECHOS_INGEST_TOKEN) and never printed.
Usage:  ECHOS_INGEST_TOKEN=... python scripts/device_client.py --base-url http://127.0.0.1:8000 --buffer buffer.jsonl --file events.jsonl
        python scripts/device_client.py --base-url ... --buffer buffer.jsonl            (just flush what is buffered)"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

Transport = Callable[[str, bytes, dict], tuple]      # (url, body, headers) -> (status, response headers dict, response body bytes); raises OSError on network failure


def http_transport(url: str, body: bytes, headers: dict, timeout: float = 15.0) -> tuple:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from None


class DeviceClient:
    def __init__(self, base_url: str, buffer_path: str | Path, token: Optional[str] = None, batch_size: int = 100, max_retries: int = 6,
                 backoff_cap_s: float = 60.0, transport: Transport = http_transport, sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print):
        self.url = base_url.rstrip("/") + "/api/ingest/events"
        self.buf, self.rej = Path(buffer_path), Path(str(buffer_path) + ".rejected")
        self.token = token if token is not None else os.environ.get("ECHOS_INGEST_TOKEN")
        self.batch_size, self.max_retries, self.cap = batch_size, max_retries, backoff_cap_s
        self.transport, self.sleep, self.log = transport, sleep, log

    # ---- buffer file
    def _read(self) -> list[str]:
        return [ln for ln in self.buf.read_text().splitlines() if ln.strip()] if self.buf.exists() else []

    def _write(self, lines: list[str]) -> None:
        tmp = self.buf.with_suffix(self.buf.suffix + ".tmp")
        tmp.write_text("".join(ln + "\n" for ln in lines))
        tmp.replace(self.buf)                                # atomic: a crash leaves the old or the new file, never half

    def add(self, events: list[dict]) -> None:
        with self.buf.open("a") as f:
            for e in events:
                f.write(json.dumps(e, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def pending(self) -> int:
        return len(self._read())

    # ---- sending
    def _post(self, lines: list[str]) -> tuple:
        body = ('{"events":[' + ",".join(lines) + "]}").encode()
        h = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        if self.token:
            h["X-Echos-Token"] = self.token
        return self.transport(self.url, body, h)

    def flush(self) -> dict:
        """Send the buffer head first, batch by batch. Returns {sent, duplicates, rejected, pending, stopped}."""
        out = {"sent": 0, "duplicates": 0, "rejected": 0, "pending": 0, "stopped": None}
        size, failures = self.batch_size, 0
        while True:
            lines = self._read()
            if not lines:
                break
            batch = lines[:size]
            try:
                status, hdr, body = self._post(batch)
            except OSError as e:
                status, hdr, body = 599, {}, str(e).encode()
            if status == 200:
                r = json.loads(body)
                out["sent"] += r.get("accepted", 0)
                out["duplicates"] += r.get("duplicates", 0)
                self._write(lines[len(batch):])
                failures, size = 0, self.batch_size
            elif status == 429 or status >= 500:
                failures += 1
                if failures > self.max_retries:
                    out["stopped"] = f"gave up after {failures - 1} retries (last status {status}); {len(lines)} events stay in the buffer"
                    break
                retry_after = {k.lower(): v for k, v in hdr.items()}.get("retry-after")
                wait = float(retry_after) if status == 429 and retry_after else min(self.cap, 2.0 ** (failures - 1))
                self.log(f"status {status}: retry {failures}/{self.max_retries} in {wait:g} s")
                self.sleep(wait)
            elif status == 413:
                if len(batch) == 1:
                    self._reject(batch, out, lines, "413 event too large")
                else:
                    size = max(1, len(batch) // 2)
            elif status == 403:
                out["stopped"] = "403: wrong or missing token, or this device_id is not registered; nothing is discarded, fix the setup and run again"
                break
            elif status in (400, 411, 422):
                if len(batch) == 1:
                    self._reject(batch, out, lines, f"{status} {body[:200].decode(errors='replace')}")
                else:
                    size = max(1, len(batch) // 2)             # bisect to find the bad event(s)
            else:
                out["stopped"] = f"unexpected status {status}"
                break
        out["pending"] = self.pending()
        return out

    def _reject(self, batch: list[str], out: dict, lines: list[str], why: str) -> None:
        with self.rej.open("a") as f:
            f.write(json.dumps({"reason": why, "event": json.loads(batch[0])}) + "\n")
        out["rejected"] += 1
        self._write(lines[1:])
        self.log(f"event rejected and moved to {self.rej.name}: {why[:80]}")

    def send(self, events: list[dict]) -> dict:
        self.add(events)
        return self.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--buffer", default="device_buffer.jsonl")
    ap.add_argument("--file", help="JSON-lines file of events to add to the buffer before sending")
    ap.add_argument("--batch-size", type=int, default=100)
    a = ap.parse_args(argv)
    c = DeviceClient(a.base_url, a.buffer, batch_size=a.batch_size)
    if a.file:
        c.add([json.loads(ln) for ln in Path(a.file).read_text().splitlines() if ln.strip()])
    r = c.flush()
    print(json.dumps(r))
    return 0 if not r["stopped"] and not r["pending"] else 1


if __name__ == "__main__":
    sys.exit(main())
