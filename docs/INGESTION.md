# Ingestion: connecting a real device or hub

`POST /api/ingest/events` is the only way data enters live mode. The emulator uses the same contract (`echos/live/schemas.py`). No real device exists yet: this page is what a device maker needs, and `scripts/demo/device_client.py` is a working reference client.

## Event format

```json
{"events": [
  {"patient_id": "p-0001", "device_id": "hub-7", "ts": "2019-05-20T08:00:00", "kind": "activity", "room": "Kitchen", "source": "device"},
  {"patient_id": "p-0001", "device_id": "bp-cuff-2", "ts": "2019-05-20T08:05:00", "kind": "vitals", "device_type": "Heart rate", "value": 71, "unit": "bpm"},
  {"patient_id": "p-0001", "device_id": "hub-7", "ts": "2019-05-20T08:15:00", "kind": "heartbeat"},
  {"patient_id": "p-0001", "device_id": "wrist-1", "ts": "2019-05-20T09:30:00", "kind": "accel", "fs": 200, "samples": [[0.0, 0.0, 1.0], "..."]}
]}
```

| Field | Rule |
|---|---|
| `patient_id`, `device_id` | 1-64 characters: letters, digits, `- _ .` only (`device_id` defaults to `device-1`). Use a pseudonymous id, never a name. |
| `ts` | **Data time**: when it happened, ISO 8601, second resolution (fractions are dropped). See *Clock rules*. |
| `kind` | `activity` (needs `room`: Kitchen, Hallway, Lounge, Bedroom, Fridge Door, Bathroom, Front Door, Back Door), `vitals` (needs `device_type` and `value`: Systolic blood pressure, Diastolic blood pressure, Heart rate, Body Temperature, Body weight; `unit` optional), `accel` (needs `fs` 10-1000 Hz and `samples`, at most 6000 rows of `[x, y, z]` in g: the impact window plus what followed), `heartbeat` (no extra fields; proves the device is alive). |
| `source` | `device`, `hub` or `emulator` (default `device`). |
| Batch | 1 to `ingest.max_batch_events` (500) events per request. |

## Responses

200 `{"accepted": n, "duplicates": m, "rejected": 0, "patients": [...]}`. Errors are `{"error": {"status": N, "message": "..."}}`.

| Status | Meaning | What the client does |
|---|---|---|
| 200 | stored (duplicates counted, not stored twice) | remove from the buffer |
| 403 | wrong or missing `X-Echos-Token`, or a `device_id` not in `ingest.devices` | stop, fix the setup; keep the data |
| 411 | no `Content-Length` (chunked uploads are refused so the size can be checked) | send with a length |
| 413 | body above `ingest.max_body_bytes` (4 MB), more than `ingest.max_batch_events` events, or one device's single batch above the rate window | send smaller batches |
| 422 | an event does not match the schema (message names the field) | fix or drop that event; the **whole batch** is refused, nothing is stored |
| 429 | a device sent more than `ingest.max_events_per_window` events (600) in `ingest.window_s` (60 s); `Retry-After` gives seconds | wait, then retry the same batch |
| 5xx / network | server or link problem | retry with backoff 1, 2, 4 ... 60 s |

Limits are checked before anything is written and apply to the whole batch, so a rejected batch is never half stored and can be re-sent unchanged.

## Idempotency and retries

An event is identified by `(patient_id, device_id, kind, ts, room or device_type)`. Sending it again is a *duplicate*, not a second row: re-send freely after a timeout or a lost response. The first value stored wins: re-sending the same vital with a different `value` and the same `ts` is counted as a duplicate and the new value is **not** stored. Events may arrive late or out of order; for a day that has already closed they are stored but do not change that day's decision.

## Clock rules

- `ts` is the time the event happened, **not** the upload time. Send it from a clock synchronised by NTP.
- A timestamp without an offset is read as the patient's local time. A timestamp with an offset (`+01:00`, `Z`) has the offset **removed without converting**, so `2019-05-20T08:00:00Z` is stored as 08:00 local. Send local wall-clock time without an offset.
- ECHOS keeps one clock per patient: the newest data time seen. A device with a wrong clock (far in the future) moves the patient's day forward and can close days early; fix the clock rather than the data.
- Activity near midnight belongs to the calendar day of its `ts`.

## Security

- Set `ECHOS_INGEST_TOKEN` on the server; devices send it as `X-Echos-Token`. Unset means **open**: do not expose an open ingest endpoint. Authentication for the web app (`--auth`) does not cover this route: the device token is separate.
- The API listens on 127.0.0.1 without TLS. Put a TLS-terminating reverse proxy in front before any device connects over a network; the token and the data travel in clear text otherwise.
- `ingest.devices` (config.yaml) restricts accepted `device_id`s. It is a list of names, not a credential: anyone with the token can use any listed name.
- The rate counters are in memory of the API process (reset on restart) and per `device_id`, which a client chooses: they stop a runaway device, not a hostile one holding the token.
- Never put names, addresses or free text into events; only the fixed fields above are accepted.

## Reference client

`scripts/demo/device_client.py` (standard library only): every event is first appended to an offline buffer file (JSON lines), then sent in batches; the buffer shrinks only after a 200. Backoff 1, 2, 4 ... 60 s on network errors and 5xx, waits `Retry-After` on 429, halves the batch on 413 or 422 to find a bad event (moved to `<buffer>.rejected`), stops on 403 without discarding anything. The token is read from `ECHOS_INGEST_TOKEN` and never printed.

```bash
ECHOS_INGEST_TOKEN=... .venv/bin/python scripts/demo/device_client.py --base-url http://127.0.0.1:8000 --buffer buffer.jsonl --file events.jsonl
```

Tested with a fake transport and against the API's test client (`tests/test_device_client.py`, `tests/test_ingest_limits.py`); **not** tested against a real device, a real network or a TLS proxy.
