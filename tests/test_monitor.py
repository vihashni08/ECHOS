"""Unit tests for the ECHOS v2 monitoring core (synthetic data only, no TIHM files needed)."""
import numpy as np
import pandas as pd
import pytest

from echos.monitor import agitation as A
from echos.monitor import allocation as AL
from echos.monitor import cohort
from echos.monitor import episode as E
from echos.monitor import personal_baseline as PB
from echos.monitor import risk as R


# ---------------------------------------------------------------- personal baseline (NIG)
def _two_patients(rng):
    a = rng.normal(130, 8, 60)
    b = rng.normal(150, 8, 60)
    return pd.Series(np.r_[a, b]), pd.Series(["a"] * 60 + ["b"] * 60)


def test_fit_prior_centres_on_patient_means():
    vals, grp = _two_patients(np.random.default_rng(0))
    p = PB.fit_prior(vals, grp)
    assert 135 < p.mu0 < 145
    assert p.beta0 / (p.alpha0 - 1) == pytest.approx(64, rel=0.4)   # E[sigma^2] ~ within-patient variance 8^2


def test_personal_scores_use_past_only():
    rng = np.random.default_rng(1)
    prior = PB.NIGPrior(140, 2, 3, 2 * 100)
    x = rng.normal(140, 10, 30)
    s1 = PB.personal_scores(x, prior)
    x2 = x.copy()
    x2[20:] += 50                                   # change the future
    s2 = PB.personal_scores(x2, prior)
    np.testing.assert_allclose(s1["z"][:20], s2["z"][:20])   # earlier scores never depend on later readings
    assert s2["z"][20] > 3                           # a jump of +50 is far above the personal usual


def test_personal_baseline_moves_from_prior_to_person():
    prior = PB.NIGPrior(140, 2, 3, 200)
    x = np.full(40, 120.0)
    s = PB.personal_scores(x, prior)
    assert s["usual"][0] == pytest.approx(140)
    assert abs(s["usual"][-1] - 120) < 2


def test_missing_reading_has_no_score():
    s = PB.personal_scores(np.array([130, np.nan, 132.0]), PB.NIGPrior(130, 2, 3, 200))
    assert np.isnan(s["z"][1]) and not np.isnan(s["usual"][1])


# ---------------------------------------------------------------- personal Bayesian rate
def test_beta_prior_matches_pooled_rate():
    rng = np.random.default_rng(2)
    rates = rng.beta(2, 8, 40)
    n = np.full(40, 60)
    k = rng.binomial(n, rates)
    p = R.fit_beta_prior(k, n)
    assert p.mean == pytest.approx(k.sum() / n.sum(), rel=1e-6)
    assert 2 < p.strength < 40


def test_rate_shrinks_new_patient_to_pool():
    prior = R.BetaPrior(2, 8)
    r = R.personal_rate(np.array([1, 1]), prior)       # two events in two days
    assert 0.2 < r[-1] < 0.5                           # not 1.0: pooled with the prior
    rr = R.personal_rate(np.ones(200), prior)
    assert rr[-1] > 0.9                                # enough own history overrides the prior


def test_forecast_and_interval():
    assert R.forecast(0.1, 3) == pytest.approx(1 - 0.9 ** 3)
    st = R.PersonalRisk(R.BetaPrior(2, 8))
    for e in (0, 1, 0, 0, 1):
        st.update(e)
    lo, hi = st.interval()
    assert lo < st.rate < hi


# ---------------------------------------------------------------- HMM (ablation model)
def _simulate(p: E.HMMParams, T: int, rng) -> np.ndarray:
    s = int(rng.random() < p.pi0)
    lab = []
    for _ in range(T):
        lab.append(int(rng.random() < (p.theta_u if s else p.theta_s)))
        s = int(rng.random() < (p.b if s else p.a))
    return np.array(lab)


def test_baum_welch_recovers_parameters():
    rng = np.random.default_rng(3)
    true = E.HMMParams(a=0.05, b=0.85, theta_s=0.02, theta_u=0.6, pi0=0.2)
    seqs = [_simulate(true, 120, rng) for _ in range(60)]
    p, ll = E.fit_hmm(seqs)
    assert all(np.diff(ll) > -1e-6)                    # EM never decreases the likelihood
    assert p.theta_u == pytest.approx(0.6, abs=0.12)
    assert p.b == pytest.approx(0.85, abs=0.1)


def test_tracker_belief_rises_after_label_and_decays():
    tr = E.EpisodeTracker(E.HMMParams(), use_personal=False)
    base = tr.step(0)
    after = tr.step(1)
    assert after > base
    later = [tr.step(0) for _ in range(10)][-1]
    assert later < after
    assert 0 <= tr.forecast(3) <= 1


# ---------------------------------------------------------------- agitation windows
def test_window_assignment():
    ts = pd.Series(pd.to_datetime(["2019-04-01 07:59", "2019-04-01 08:00", "2019-04-01 13:00", "2019-04-01 19:00"]))
    w = A.window_end(ts)
    assert list(w.dt.strftime("%m-%d %H")) == ["04-01 08", "04-01 12", "04-01 18", "04-02 08"]
    lab = A.label_window(pd.Series(pd.to_datetime(["2019-04-01 12:00:30", "2019-04-01 09:00:00", "2019-04-01 07:00:00"])))
    assert list(lab.dt.strftime("%m-%d %H")) == ["04-01 12", "04-01 08", "03-31 18"]


def _activity(n_days=20, burst_day=15):
    rows = []
    for d in range(n_days):
        day = pd.Timestamp("2019-04-01") + pd.Timedelta(days=d)
        n = 40 if d == burst_day else 10
        for i in range(n):
            rows.append(("p1", "Kitchen" if i % 2 else "Hallway", day + pd.Timedelta(hours=9, minutes=i)))
    return pd.DataFrame(rows, columns=["patient_id", "location_name", "date"])


def test_personal_slot_z_flags_burst_and_is_past_only():
    f = A.add_personal_slot_z(A.window_features(_activity()))
    burst = f[(f.w == pd.Timestamp("2019-04-16 12:00"))]
    assert float(burst["total_z"].iloc[0]) > 3
    f2 = A.add_personal_slot_z(A.window_features(_activity(burst_day=19)))
    same = f2[f2.w == pd.Timestamp("2019-04-16 12:00")]
    assert float(same["total_z"].iloc[0]) == pytest.approx(0.0, abs=1e-9)   # a later burst never changes an earlier score


def test_history_is_strictly_before_window():
    f = A.window_features(_activity())
    labels = pd.DataFrame({"patient_id": ["p1"], "date": [pd.Timestamp("2019-04-10 12:00")], "type": ["Agitation"]})
    f = A.add_history(A.attach_labels(f, labels))
    row = f[f.w == pd.Timestamp("2019-04-10 12:00")].iloc[0]
    nxt = f[f.w == pd.Timestamp("2019-04-10 18:00")].iloc[0]
    assert row.y == 1 and row.hist_episodes == 0
    assert nxt.hist_episodes == 1 and nxt.windows_since_episode == 0


# ---------------------------------------------------------------- allocation + safety
def test_priority_uses_lift_over_base_rate():
    assert AL.priority(0.6, 0.0, v_bar=0.3, a_bar=0.02) == pytest.approx(2.0)
    assert AL.priority(0.3, 0.1, v_bar=0.3, a_bar=0.02) == pytest.approx(5.0)   # agitation at 5x its base rate wins
    assert AL.priority(None, float("nan"), 0.3, 0.02) == 0.0


def test_select_budget_fairness_and_determinism():
    cands = [AL.Candidate(f"p{i}", priority=i, days_since_review=0) for i in range(10)]
    cands.append(AL.Candidate("old", priority=0.1, days_since_review=9))
    out = AL.select(cands, 4, fairness_days=7)
    ids = [c.patient_id for c, _ in out]
    assert len(ids) == 4 and ids[0] == "old" and dict((c.patient_id, r) for c, r in out)["old"] == "fairness"
    assert ids[1:] == ["p9", "p8", "p7"]
    assert AL.select(cands, 4, 7) == out
    assert [c.patient_id for c, _ in AL.select(cands, 3, None)] == ["p9", "p8", "p7"]


def test_urgent_flags_red_limits():
    assert cohort.urgent_flags({"sys": 182, "dia": 95, "hr": 70}) == ["systolic 182 at or above 180 mmHg"]
    assert cohort.urgent_flags({"sys": 150, "dia": 85, "hr": 131})[0].startswith("pulse 131")
    assert cohort.urgent_flags({"sys": 150, "dia": 85, "hr": 102}) == []          # TIHM attention, not urgent
    assert cohort.urgent_flags({"sys": float("nan"), "temp": 34.9})[0].startswith("temperature 34.9")


def test_safety_flags_tihm_limits():
    assert cohort.safety_flags({"sys": 195, "dia": 80, "hr": 70}) == ["systolic 195 outside 80-190 mmHg"]
    assert cohort.safety_flags({"sys": 150, "dia": 85, "hr": 52})[0].startswith("pulse 52")
    assert cohort.safety_flags({"sys": float("nan"), "temp": 38.0})[0].startswith("temperature 38.0")
    assert cohort.safety_flags({"sys": 140, "dia": 80, "hr": 70}) == []


def test_models_refuse_non_tihm_rows():
    from echos.monitor.vitals_model import fit_vitals_model
    df = pd.DataFrame({"patient_id": ["a"], "day": [pd.Timestamp("2019-04-01")], "sys": [140.0], "dia": [80.0], "hr": [70.0],
                       "event": [0], "has_reading": [1], "source": ["synthetic"]})
    with pytest.raises(AssertionError):
        fit_vitals_model(df)
    with pytest.raises(AssertionError):
        A.AgitationModel(["total"]).fit(pd.DataFrame({"total": [1], "y": [0], "patient_id": ["a"], "source": ["synthetic"]}))
