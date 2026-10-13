"""Live / emulator mode. Raw sensor events arrive through the ingestion API (POST /api/ingest/events); detectors are scored FROM THOSE EVENTS
(not from the pre-built training table) and triage re-decides as data arrives. Sources are adapters: the emulator (labelled, replays real TIHM raw
events in wall-clock time) is the one shipped; a real hub would call the same endpoint."""
