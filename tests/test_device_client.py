"""Reference device client: batching, retry with backoff, Retry-After, offline buffer, bad events. Fake transport; no network."""
import json

import pytest

from scripts.device_client import DeviceClient


def ev(i):
    return {"patient_id": "p1", "device_id": "d1", "ts": f"2019-05-20T08:{i // 60:02d}:{i % 60:02d}", "kind": "activity", "room": "Kitchen"}


class Fake:
    """Scripted server: answers = list of status codes or callables(batch)->(status, headers, body); the rest are 200."""
    def __init__(self, answers=()):
        self.answers, self.calls, self.stored = list(answers), [], []

    def __call__(self, url, body, headers):
        evs = json.loads(body)["events"]
        self.calls.append((len(evs), headers))
        a = self.answers.pop(0) if self.answers else 200
        if a == "net":
            raise OSError("connection refused")
        if callable(a):
            return a(evs)
        if isinstance(a, tuple):
            return a[0], a[1], b"{}"
        if a == 200:
            self.stored += evs
            return 200, {}, json.dumps({"accepted": len(evs), "duplicates": 0}).encode()
        return a, {}, b'{"error":{"message":"x"}}'


def client(tmp_path, fake, **kw):
    sleeps = []
    return DeviceClient("http://x", tmp_path / "b.jsonl", token="t", sleep=sleeps.append, log=lambda *_: None, transport=fake, **kw), sleeps


def test_batches_and_empties_the_buffer(tmp_path):
    f = Fake()
    c, _ = client(tmp_path, f, batch_size=100)
    r = c.send([ev(i) for i in range(250)])
    assert [n for n, _ in f.calls] == [100, 100, 50] and r["sent"] == 250 and r["pending"] == 0 and len(f.stored) == 250
    assert f.calls[0][1]["X-Echos-Token"] == "t" and f.calls[0][1]["Content-Length"]


def test_offline_keeps_events_and_a_later_flush_sends_them_in_order(tmp_path):
    f = Fake(["net"] * 20)
    c, sleeps = client(tmp_path, f, max_retries=3)
    r = c.send([ev(i) for i in range(5)])
    assert r["stopped"] and r["pending"] == 5 and r["sent"] == 0
    assert sleeps == [1.0, 2.0, 4.0]                               # backoff 1, 2, 4
    c2, _ = client(tmp_path, Fake())                                # a new process: same buffer file
    c2.transport = f2 = Fake()
    r2 = c2.flush()
    assert r2["sent"] == 5 and r2["pending"] == 0 and [e["ts"] for e in f2.stored] == [ev(i)["ts"] for i in range(5)]


def test_5xx_then_success_and_backoff_cap(tmp_path):
    f = Fake([503, 500, 502, 200])
    c, sleeps = client(tmp_path, f, backoff_cap_s=2)
    assert c.send([ev(0)])["sent"] == 1 and sleeps == [1.0, 2.0, 2.0]


def test_429_waits_retry_after(tmp_path):
    f = Fake([(429, {"Retry-After": "41"}), 200])
    c, sleeps = client(tmp_path, f)
    assert c.send([ev(0)])["sent"] == 1 and sleeps == [41.0]


def test_413_halves_the_batch_and_a_single_oversized_event_is_rejected(tmp_path):
    f = Fake([413, 200, 200])
    c, _ = client(tmp_path, f, batch_size=4)
    r = c.send([ev(i) for i in range(4)])
    assert [n for n, _ in f.calls] == [4, 2, 2] and r["sent"] == 4
    f2 = Fake([413])
    c2, _ = client(tmp_path, f2, batch_size=1)
    r2 = c2.send([ev(9)])
    assert r2["rejected"] == 1 and r2["pending"] == 0 and (tmp_path / "b.jsonl.rejected").exists()


def test_bad_event_is_found_by_bisection_and_good_ones_still_go(tmp_path):
    bad = ev(2)

    def server(evs):
        if bad in evs:
            return 422, {}, b'{"error":{"message":"room"}}'
        return 200, {}, json.dumps({"accepted": len(evs), "duplicates": 0}).encode()
    f = Fake([server] * 50)
    c, _ = client(tmp_path, f, batch_size=4)
    r = c.send([ev(i) for i in range(4)])
    assert r["sent"] == 3 and r["rejected"] == 1 and r["pending"] == 0
    rej = [json.loads(ln) for ln in (tmp_path / "b.jsonl.rejected").read_text().splitlines()]
    assert rej[0]["event"] == bad and rej[0]["reason"].startswith("422")


def test_403_stops_and_keeps_everything(tmp_path):
    c, sleeps = client(tmp_path, Fake([403]))
    r = c.send([ev(i) for i in range(3)])
    assert "403" in r["stopped"] and r["pending"] == 3 and sleeps == [] and not (tmp_path / "b.jsonl.rejected").exists()


def test_token_comes_from_the_environment_and_is_never_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOS_INGEST_TOKEN", "secret-xyz")
    logs, f = [], Fake([503, 200])
    c = DeviceClient("http://x", tmp_path / "b.jsonl", sleep=lambda s: None, log=logs.append, transport=f)
    c.send([ev(0)])
    assert f.calls[0][1]["X-Echos-Token"] == "secret-xyz" and not any("secret-xyz" in str(x) for x in logs)
