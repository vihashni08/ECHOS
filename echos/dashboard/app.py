"""ECHOS dashboards (Streamlit). Reads ONLY from SQLite (data/echos.db) and reports/. Run: streamlit run echos/dashboard/app.py"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
import yaml  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
DB = ROOT / CFG["db_path"]
ICON = {"NORMAL": "🟢 Normal", "MONITOR": "🟠 Changes noticed", "URGENT": "🔴 Needs attention"}

st.set_page_config(page_title="ECHOS", layout="wide")


@st.cache_resource
def conn():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)


def q(sql, params=()):
    return pd.read_sql_query(sql, conn(), params=params)


def patients():
    return q("select distinct patient_id from case_state order by 1").patient_id.tolist()


def facts_for(pid, upto=None):
    df = q("select day, facts from daily_facts where patient_id=? order by day", (pid,))
    if upto:
        df = df[df.day <= upto]
    return [json.loads(f) for f in df.facts]


def report_text(pid, day, kind):
    r = q("select path from reports where patient_id=? and day=? and kind=?", (pid, day, kind))
    if r.empty:
        return None
    p = ROOT / r.path[0]
    return p.read_text() if p.exists() else None


def caregiver(pid):
    days = q("select day, status, data_gap from case_state where patient_id=? order by day", (pid,))
    day = st.selectbox("Day", days.day.tolist()[::-1], index=0)
    row = days[days.day == day].iloc[0]
    st.subheader(ICON[row.status])
    if row.data_gap:
        st.caption("Some data was missing today; see the data note in the report.")
    txt = report_text(pid, day, "daily")
    st.markdown(txt or "_No daily report stored for this day._")
    st.markdown("#### Last 7 statuses")
    last = days[days.day <= day].tail(7)
    st.dataframe(last.assign(status=last.status.map(ICON)).rename(columns={"day": "Day", "status": "Status", "data_gap": "Data gap"}),
                 hide_index=True, width="stretch")
    st.markdown("#### Alerts")
    al = q("select day, level, reasons from alerts where patient_id=? order by day desc limit 10", (pid,))
    if al.empty:
        st.write("No alerts.")
    else:
        al["reasons"] = al.reasons.map(lambda r: "; ".join(json.loads(r)))
        st.dataframe(al, hide_index=True, width="stretch")


def clinician(pid):
    wk = q("select day, mode, path from reports where patient_id=? and kind='weekly' order by day desc", (pid,))
    if wk.empty:
        st.info("No weekly report yet for this patient.")
    else:
        day = st.selectbox("Week ending", wk.day.tolist())
        p = ROOT / wk[wk.day == day].path.iloc[0]
        if p.exists():
            components.html(p.read_text(), height=900, scrolling=True)
    fx = facts_for(pid)
    if not fx:
        return
    st.markdown("#### Vitals trends (daily, with personal baseline)")
    idx = pd.to_datetime([f["day"] for f in fx])
    cols = st.columns(2)
    for i, (title, keys) in enumerate([("Blood pressure (mmHg)", ["sys", "dia"]), ("Heart rate (bpm)", ["hr"]),
                                       ("Temperature (°C)", ["temp"]), ("Weight (kg)", ["weight"])]):
        d = pd.DataFrame(index=idx)
        for k in keys:
            d[k] = [f["vitals"][k]["value"] for f in fx]
            d[k + " baseline"] = [f["vitals"][k]["baseline"] for f in fx]
        with cols[i % 2]:
            st.caption(title)
            st.line_chart(d)
    st.markdown("#### Alert / agitation timeline")
    rows = [{"day": f["day"], "id": fl["id"], "finding": fl["type"].replace("_", " "), "status": f["status"],
             "evidence": json.dumps(fl["evidence"])} for f in fx for fl in f["flags"] if fl["type"] != "data_gap"]
    tl = pd.DataFrame(rows)
    if tl.empty:
        st.write("No flags raised.")
    else:
        st.bar_chart(tl.groupby(["day", "finding"]).size().unstack(fill_value=0))
        st.markdown("#### Evidence table")
        st.dataframe(tl, hide_index=True, width="stretch")


def health():
    st.markdown("#### Agent heartbeat health")
    hb = q("select agent_id, status, pid, ts, last_event_id from agent_heartbeat order by agent_id")
    if hb.empty:
        st.write("No heartbeats recorded.")
    else:
        hb["seconds_since_heartbeat"] = (time.time() - hb.ts).round(0)
        hb["last heartbeat"] = pd.to_datetime(hb.ts, unit="s").dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(hb.drop(columns="ts"), hide_index=True, width="stretch")
        st.caption("Heartbeats are from the last pipeline run; a long gap means no agent process is currently running.")
    rl = q("select agent_id, reason, detected_ts, restarted_ts from restart_log order by id desc limit 10")
    st.write("Supervisor restarts:", "none" if rl.empty else "")
    if not rl.empty:
        st.dataframe(rl, hide_index=True, width="stretch")


st.title("ECHOS: home health observation support")
st.caption("Decision-support only. Not a medical assessment.")
if not DB.exists():
    st.error("No database yet. Run: PYTHONPATH=. python3 scripts/run_demo.py")
    st.stop()
pats = patients()
page = st.sidebar.radio("Page", ["Caregiver", "Clinician"])
pid = st.sidebar.selectbox("Patient", pats) if pats else None
if pid is None:
    st.warning("No patients processed yet.")
elif page == "Caregiver":
    caregiver(pid)
else:
    clinician(pid)
    health()
