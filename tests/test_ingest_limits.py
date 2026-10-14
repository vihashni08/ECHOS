"""Server-side limits on POST /api/ingest/events. No network; fake clock for the rate window."""
import pytest
from fastapi.testclient import TestClient

from echos.api import ingest_limits as IL
from echos.api import services as V
from echos.api.app import create_app
from echos.config import load_config
from echos.orchestrator import state as S

URL = "/api/ingest/events"


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def ev(i=0, dev="dev-1", pid="px"):
    return {"patient_id": pid, "device_id": dev, "ts": f"2019-05-20T08:{i // 60:02d}:{i % 60:02d}", "kind": "activity", "room": "Kitchen"}


def make(tmp_path, **ingest):
    cfg = load_config()
    cfg["ingest"] = {**cfg.get("ingest", {}), **ingest}
    app = create_app(V.Ctx(eng=S.get_engine(tmp_path / "l.db"), root=tmp_path, cfg=cfg))
    clk = Clock()
    app.state.ingest_guard.clock = clk
    return TestClient(app), clk


def test_defaults_accept_a_normal_batch(tmp_path):
    c, _ = make(tmp_path)
    r = c.post(URL, json={"events": [ev(i) for i in range(50)]})
    assert r.status_code == 200 and r.json()["accepted"] == 50


def test_too_many_events_is_413_and_nothing_is_stored(tmp_path):
    c, _ = make(tmp_path, max_batch_events=10)
    r = c.post(URL, json={"events": [ev(i) for i in range(11)]})
    assert r.status_code == 413 and "limit is 10" in r.json()["error"]["message"]
    assert c.post(URL, json={"events": [ev(i) for i in range(10)]}).json()["accepted"] == 10      # exactly at the limit is fine
    assert c.get("/api/live/status").json()["patients"][0]["patient_id"] == "px"


def test_too_large_body_is_413(tmp_path):
    c, _ = make(tmp_path, max_body_bytes=500)
    r = c.post(URL, json={"events": [ev(i) for i in range(20)]})
    assert r.status_code == 413 and "bytes" in r.json()["error"]["message"]
    assert c.post(URL, json={"events": [ev(0)]}).status_code == 200


def test_missing_content_length_is_411(tmp_path):
    c, _ = make(tmp_path)
    r = c.post(URL, content=iter([b'{"events": []}']), headers={"Content-Type": "application/json"})      # chunked: no Content-Length
    assert r.status_code == 411


def test_unregistered_device_is_403_registered_ok(tmp_path):
    c, _ = make(tmp_path, devices=["dev-1", "dev-2"])
    assert c.post(URL, json={"events": [ev(0, "dev-1"), ev(1, "dev-2")]}).status_code == 200
    r = c.post(URL, json={"events": [ev(2, "dev-1"), ev(3, "intruder")]})
    assert r.status_code == 403 and "intruder" in r.json()["error"]["message"] and "dev-1" not in r.json()["error"]["message"].split("registered:")[1].split("(")[0]
    assert c.post(URL, json={"events": [ev(2, "dev-1")]}).json()["accepted"] == 1            # the rejected batch stored nothing, the good event still goes in


def test_empty_device_list_means_open(tmp_path):
    c, _ = make(tmp_path, devices=[])
    assert c.post(URL, json={"events": [ev(0, "anything-1")]}).status_code == 200


def test_rate_limit_per_device_429_with_retry_after_and_window_reopens(tmp_path):
    c, clk = make(tmp_path, max_events_per_window=10, window_s=60)
    assert c.post(URL, json={"events": [ev(i) for i in range(6)]}).status_code == 200
    clk.t += 20
    assert c.post(URL, json={"events": [ev(i) for i in range(6, 10)]}).status_code == 200            # 10 of 10 used
    r = c.post(URL, json={"events": [ev(10)]})
    assert r.status_code == 429 and int(r.headers["retry-after"]) == 41                                # the first 6 leave the window at t+60
    assert "retry in 41 s" in r.json()["error"]["message"]
    other = c.post(URL, json={"events": [ev(11, dev="dev-2")]})                                         # another device is not affected
    assert other.status_code == 200
    clk.t += 41
    assert c.post(URL, json={"events": [ev(10), ev(11)]}).status_code == 200                          # the oldest 6 have left


def test_one_batch_larger_than_the_window_is_413_not_endless_429(tmp_path):
    c, _ = make(tmp_path, max_events_per_window=5)
    r = c.post(URL, json={"events": [ev(i) for i in range(6)]})
    assert r.status_code == 413 and "smaller batches" in r.json()["error"]["message"]


def test_rejected_batch_does_not_use_up_the_rate_budget(tmp_path):
    c, _ = make(tmp_path, max_events_per_window=5, devices=["dev-1"])
    for _ in range(3):
        assert c.post(URL, json={"events": [ev(0, "bad")]}).status_code == 403
    assert c.post(URL, json={"events": [ev(i) for i in range(5)]}).status_code == 200


def test_token_check_still_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOS_INGEST_TOKEN", "tok-123")
    c, _ = make(tmp_path)
    assert c.post(URL, json={"events": [ev(0)]}).status_code == 403
    assert c.post(URL, json={"events": [ev(0)]}, headers={"X-Echos-Token": "tok-123"}).status_code == 200


def test_guard_unit_defaults():
    g = IL.IngestGuard({})
    assert (g.max_batch, g.max_rate, g.devices) == (500, 600, set())
    with pytest.raises(IL.LimitError) as e:
        g.check_size("abc")
    assert e.value.status == 400


def test_reference_client_against_the_real_api_handles_429_and_413(tmp_path):
    from scripts.device_client import DeviceClient
    c, clk = make(tmp_path, max_events_per_window=20, max_batch_events=8, window_s=60)

    def transport(url, body, headers):
        r = c.post("/api/ingest/events", content=body, headers=headers)
        return r.status_code, dict(r.headers), r.content

    def sleep(s):
        clk.t += s
    dc = DeviceClient("http://x", tmp_path / "buf.jsonl", batch_size=10, sleep=sleep, log=lambda *_: None, transport=transport)
    r = dc.send([ev(i) for i in range(30)])            # 10 > max batch 8 -> 413 -> halves to 5; 20 per minute -> 429 -> waits Retry-After
    assert r["sent"] == 30 and r["pending"] == 0 and r["stopped"] is None and r["rejected"] == 0
