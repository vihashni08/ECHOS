"""SQLite message bus between agents. post() never blocks; poll() returns NEW messages for a recipient; done() marks them consumed.
Every hand-over (Triage -> Investigator -> Coordinator -> Report) is a row, so the UI can show who told whom what."""
from __future__ import annotations

import json
from typing import Iterable, Optional

from sqlalchemy import select

from echos.orchestrator import state as S


def post(eng, sender: str, recipient: str, topic: str, patient_id: str = "", day: str = "", payload: Optional[dict] = None) -> int:
    with eng.begin() as c:
        r = c.execute(S.agent_message.insert().values(ts=S.now(), sender=sender, recipient=recipient, topic=topic, patient_id=patient_id, day=day,
                                                      payload=S.j(payload or {}), status="NEW"))
    return r.inserted_primary_key[0]


def poll(eng, recipient: str, topics: Optional[Iterable[str]] = None, limit: int = 50) -> list:
    q = select(S.agent_message).where((S.agent_message.c.recipient == recipient) & (S.agent_message.c.status == "NEW")).order_by(S.agent_message.c.id).limit(limit)
    if topics:
        q = q.where(S.agent_message.c.topic.in_(list(topics)))
    with eng.begin() as c:
        rows = c.execute(q).fetchall()
    return [{"id": r.id, "ts": r.ts, "sender": r.sender, "recipient": r.recipient, "topic": r.topic, "patient_id": r.patient_id, "day": r.day, "payload": json.loads(r.payload or "{}")} for r in rows]


def prune(eng, keep_s: float, now: Optional[float] = None) -> int:
    """Delete handled (DONE) messages and broadcasts (recipient 'all', never consumed) older than keep_s seconds. Unhandled NEW messages to an
    agent are never deleted. Returns the number removed. The Agent activity page keeps `bus.keep_s` of history (default 30 days)."""
    cutoff = (now if now is not None else S.now()) - keep_s
    with eng.begin() as c:
        r = c.execute(S.agent_message.delete().where((S.agent_message.c.ts < cutoff) & ((S.agent_message.c.status == "DONE") | (S.agent_message.c.recipient == "all"))))
    return r.rowcount or 0


def done(eng, ids: Iterable[int]) -> None:
    ids = list(ids)
    if ids:
        with eng.begin() as c:
            c.execute(S.agent_message.update().where(S.agent_message.c.id.in_(ids)).values(status="DONE"))
