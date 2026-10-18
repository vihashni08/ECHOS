"""Fault demo against the REAL supervisor through the API (run the backend with scripts/run_api.sh --dry --no-llm --demo --db <throw-away db>).
For each agent: POST /agents/{name}/kill, measure the time until its heartbeat is stale and until it is alive again, inject a fall DURING the outage and
check that exactly one alert (two DryRun outbox rows) exists for it afterwards. Usage: PYTHONPATH=. python research/api_kill_check.py [base_url]"""
import sys
import threading
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000") + "/api"


def main() -> int:
    c = httpx.Client(timeout=130)
    for _ in range(60):
        try:
            if c.get(f"{BASE}/sim/state").json()["runtime_alive"]:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    if not c.get(f"{BASE}/system/config").json()["allow_kill"]:
        print("allow_kill is off: start the backend with --demo")
        return 2
    c.post(f"{BASE}/sim/control", json={"action": "reset"})
    c.post(f"{BASE}/sim/control", json={"action": "speed", "speed": 0.1})
    c.post(f"{BASE}/sim/control", json={"action": "play"})
    time.sleep(3)
    pats = [p["patient_id"] for p in c.get(f"{BASE}/patients").json()["items"]]
    print(f"{'agent':<18}{'max_hb_age_s':>13}{'stale>15s_at_s':>15}{'recovered_s':>12}{'alert_s':>9} {'alerts':>6} {'outbox':>6}")
    for i, agent in enumerate(sys.argv[2].split(",") if len(sys.argv) > 2 else ("fall", "quality", "health_monitoring")):
        res = {}
        t0 = time.time()
        th = threading.Thread(target=lambda: res.update(httpx.post(f"{BASE}/agents/{agent}/kill?wait_s=150", timeout=160).json()))
        th.start()
        time.sleep(1.0)
        inj = c.post(f"{BASE}/sim/fall", json={"patient_id": pats[i % len(pats)], "subject": "SA03", "activity": "F02"}).json()
        aid = f"{inj['patient_id']}|{inj['day']}|fall|{inj['event_id']}"
        max_age, t_stale, t_alert, n_al, n_ob = 0.0, None, None, 0, 0
        while time.time() - t0 < 200 and (th.is_alive() or t_alert is None):
            h = next(x for x in c.get(f"{BASE}/agents/health").json() if x["agent"] == agent)
            age = h["heartbeat_age_s"] or 0.0
            max_age = max(max_age, age)
            if t_stale is None and age > 15:
                t_stale = time.time() - t0            # more than 3 missed heartbeats (5 s each)
            if t_alert is None:
                al = [a for a in c.get(f"{BASE}/alerts?limit=500").json()["items"] if a["alert_id"] == aid]
                if al:
                    t_alert, n_al = time.time() - t0, len(al)
                    n_ob = sum(o["alert_id"] == aid for o in c.get(f"{BASE}/outbox?limit=500").json()["items"])
            time.sleep(0.5)
        th.join(5)
        f = lambda v: "never" if v is None else f"{v:.1f}"  # noqa: E731
        print(f"{agent:<18}{max_age:>13.1f}{f(t_stale):>15}{f(res.get('recovery_s')):>12}{f(t_alert):>9} {n_al:>6} {n_ob:>6}  recovered={res.get('recovered')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
