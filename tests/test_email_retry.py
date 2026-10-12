"""Alert email delivery hardening (FALL / CRITICAL_VITALS): PENDING -> RETRYING -> SENT | FAILED, exponential backoff capped at 60 s inside a 15-minute window,
a sweeper that retries FAILED rows every 5 minutes for 2 hours, restart survival, no duplicates, never blocking the fall path. FakeMailer and a FAKE CLOCK only."""
import time

import pytest
from sqlalchemy import select

from echos.alerts import payload as P
from echos.alerts.dispatcher import AlertDispatcher
from echos.alerts.mailer import FakeMailer
from echos.orchestrator import state as S

TO = {"caregiver": "care@example.test", "clinician": "doc@example.test"}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = max(self.t, t)


class Outage(FakeMailer):
    """Every send fails while the (fake) clock is inside [t0, t1)."""
    def __init__(self, clock, t0, t1):
        super().__init__()
        self.clock, self.t0, self.t1 = clock, t0, t1

    def send(self, to, subject, body, body_html=None, attachments=None):
        if self.t0 <= self.clock() < self.t1:
            self.attempts.append((to, subject))
            raise ConnectionError("fake SMTP outage")
        super().send(to, subject, body, body_html, attachments)


def fall(eid=1):
    return P.AlertPayload(alert_id=f"p1|2019-05-05|fall|{eid}", alert_type="FALL", patient_id="p1", event_time="2019-05-05T10:00:00", event_id=eid,
                          fall_probability=0.97, peak_impact_g=5.2, post_fall_inactivity=True)


@pytest.fixture
def eng(tmp_path):
    return S.get_engine(tmp_path / "r.db")


def rows(eng):
    with eng.begin() as c:
        return c.execute(select(S.outbox).order_by(S.outbox.c.outbox_id)).fetchall()


def make(eng, mailer, clock, **kw):
    return AlertDispatcher(eng, mailer, to=TO, clock=clock, threaded=False, retry_window_s=900, sweep_interval_s=300, sweep_window_s=7200, **kw)


@pytest.mark.parametrize("outage_s,attempts_at_least", [(8, 5), (60, 7)])
def test_short_outages_recover_by_backoff_and_states_are_visible(eng, outage_s, attempts_at_least):
    clk = Clock()
    m = Outage(clk, clk.t, clk.t + outage_s)
    d = make(eng, m, clk)
    d.dispatch(fall())
    d.run_due()
    r = rows(eng)[0]
    assert r.status == "RETRYING" and r.attempts == 1 and r.next_attempt_time == clk.t + 1 and r.window_start == clk.t       # state is in the table
    seen = set()
    for _ in range(300):
        d.drive(clk.t + 1)
        seen |= {x.status for x in rows(eng)}
        if all(x.status == "SENT" for x in rows(eng)):
            break
    assert {x.status for x in rows(eng)} == {"SENT"} and all(x.attempts >= attempts_at_least for x in rows(eng))
    assert "RETRYING" in seen and len(m.sent) == 2                                              # one email per recipient
    with eng.begin() as c:
        assert c.execute(select(S.alerts.c.status)).scalar() == "SENT"


def test_backoff_doubles_and_is_capped_at_60_seconds(eng):
    clk = Clock()
    d = make(eng, Outage(clk, clk.t, clk.t + 1e9), clk)
    d.dispatch(fall())
    waits, last = [], None
    for _ in range(14):
        d.run_due()
        r = rows(eng)[0]
        if r.status != "RETRYING":
            break
        waits.append(round(r.next_attempt_time - clk.t))
        clk.set(r.next_attempt_time)
    assert waits[:8] == [1, 2, 4, 8, 16, 32, 60, 60] and max(waits) == 60


def test_twenty_minute_outage_ends_failed_then_the_sweeper_succeeds_after_recovery(eng):
    clk = Clock()
    t0 = clk.t
    m = Outage(clk, t0, t0 + 20 * 60)
    d = make(eng, m, clk)
    d.dispatch(fall())
    d.drive(t0 + 16 * 60)                                                                      # beyond the 15-minute retry window
    r = rows(eng)
    assert {x.status for x in r} == {"FAILED"} and all(x.attempts >= 10 for x in r) and not m.sent
    with eng.begin() as c:
        assert c.execute(select(S.alerts.c.status)).scalar() == "FAILED"                       # a failed delivery never looks delivered
    d.drive(t0 + 19 * 60)
    assert {x.status for x in rows(eng)} == {"FAILED"}                                         # sweeps during the outage fail again and stay FAILED
    d.drive(t0 + 26 * 60)                                                                      # mailer recovered at 20 min: the next sweep delivers
    assert {x.status for x in rows(eng)} == {"SENT"} and len(m.sent) == 2
    assert min(x.sent_time for x in rows(eng)) >= t0 + 20 * 60


def test_sweeper_gives_up_after_two_hours(eng):
    clk = Clock()
    t0 = clk.t
    m = Outage(clk, t0, t0 + 3 * 3600)
    d = make(eng, m, clk)
    d.dispatch(fall())
    d.drive(t0 + 2 * 3600 + 400)
    n = len(m.attempts)
    d.drive(t0 + 2 * 3600 + 1500)
    assert len(m.attempts) == n and {x.status for x in rows(eng)} == {"FAILED"}                  # no more sweeps after sweep_window_s


def test_no_duplicate_email_once_sent(eng):
    clk = Clock()
    m = FakeMailer()
    d = make(eng, m, clk)
    d.dispatch(fall())
    d.run_due()
    assert len(m.sent) == 2 and {x.status for x in rows(eng)} == {"SENT"}
    oid = rows(eng)[0].outbox_id
    d._push(oid, 0.0, d._job(rows(eng)[0]))                                                     # a stale duplicate job (e.g. recover + sweeper)
    d.run_due()
    d.sweep()
    assert d.recover() == 0 and len(m.sent) == 2
    with pytest.raises(ValueError):
        d.retry_row(oid)
    assert not d.dispatch(fall())                                                                # same event: deduplicated
    d.run_due()
    assert len(m.sent) == 2


def test_retries_survive_a_process_restart(eng):
    clk = Clock()
    t0 = clk.t
    m1 = Outage(clk, t0, t0 + 30)
    d1 = make(eng, m1, clk)
    d1.dispatch(fall())
    d1.drive(t0 + 5)
    before = rows(eng)[0]
    assert before.status == "RETRYING" and before.attempts >= 3
    del d1                                                                                      # the process dies: nothing but the outbox table remains
    clk.set(t0 + 6)
    m2 = Outage(clk, t0, t0 + 30)
    d2 = make(eng, m2, clk)
    assert d2.recover() == 2
    after = rows(eng)[0]
    assert after.window_start == before.window_start and after.attempts == before.attempts       # window and attempt count are not reset
    d2.drive(t0 + 100)
    assert {x.status for x in rows(eng)} == {"SENT"} and len(m2.sent) == 2 and all(x.attempts > before.attempts for x in rows(eng))


def test_manual_retry_resends_one_row_and_respects_the_mailer(eng):
    clk = Clock()
    t0 = clk.t
    m = Outage(clk, t0, t0 + 1e9)
    d = make(eng, m, clk)
    d.dispatch(fall())
    d.drive(t0 + 1000)
    oid = next(x.outbox_id for x in rows(eng) if x.recipient_role == "clinician")
    m.t1 = clk.t                                                                                # recovered
    assert d.retry_row(oid) == "RETRYING"
    d.run_due()
    st = {x.recipient_role: x.status for x in rows(eng)}
    assert st["clinician"] == "SENT" and len(m.sent) == 1 and m.sent[0]["to"] == TO["clinician"]
    assert next(x for x in rows(eng) if x.recipient_role == "clinician").window_start >= t0 + 1000                                                # a fresh retry window


def test_weekly_mail_keeps_three_attempts(eng, tmp_path):
    clk = Clock()
    m = Outage(clk, clk.t, clk.t + 1e9)
    d = AlertDispatcher(eng, m, to=TO, clock=clk, threaded=False, backoff_s=[1, 2, 4])
    pdf = tmp_path / "w.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    d.dispatch_weekly("p1", "2019-W19", "s", "b", "<p>b</p>", str(pdf), "2019-05-12")
    d.drive(clk.t + 60)
    w = rows(eng)[0]
    assert w.status == "FAILED" and w.attempts == 3
    d.drive(clk.t + 4000)
    assert rows(eng)[0].attempts == 3                                                            # the sweeper never touches weekly mails


# ---------------------------------------------------------------- real threads: the fall path is never blocked while retrying
def test_producer_and_orchestrator_are_not_blocked_while_mail_is_retrying(tmp_path):
    from echos.orchestrator.graph import build_graph, process_case
    from tests.test_orchestrator import R, health, qual
    from tests.test_safety import _seed_case
    eng = S.get_engine(tmp_path / "t.db")
    m = FakeMailer(fail_for={"*"})
    d = AlertDispatcher(eng, m, to=TO)                                                           # real clock, real threads, default 15-minute window
    t0 = time.time()
    for i in range(1, 6):
        assert d.dispatch(fall(i))
    assert time.time() - t0 < 1.0                                                                 # five alerts queued while every send fails
    _seed_case(eng, "2019-05-05", {"quality": qual(), "health_monitoring": health(), "fall": R(0.97, post_fall_inactive=True)})
    g, conn = build_graph(eng, tmp_path / "cp.sqlite", report_llm=None, report_root=tmp_path, gate=True, dispatcher=d, weekly_pdf=False)
    t1 = time.time()
    assert process_case(g, "p1", "2019-05-05")
    assert time.time() - t1 < 15
    with eng.begin() as c:
        assert c.execute(select(S.case_state.c.status)).scalar() == "URGENT"
        assert c.execute(select(S.alerts.c.alert_id)).fetchall()
    assert {x.status for x in rows(eng)} <= {"PENDING", "RETRYING"}                              # still retrying, never reported as sent
    d.close(0.1)
    conn.close()
