"""Triage stage 2: nearest-threshold ratio r = max_k p_k / tau_k, routing rule with a budget-chosen cutoff theta, safety of the alert owner."""
import pytest

from echos.team import planner as PL
from echos.team.planner import AgentPick, detector_scores, nearest, plan_team

T = {"vitals_prob": 0.278, "fall_prob": 0.8}


def res(vit=None, fall=None, degraded=False, fixed=False):
    return {"health": {"degraded": degraded, "vitals": {"available": True, "probability": vit, "fixed_rule_triggered": fixed} if vit is not None or fixed else {}},
            "fall": {"score": fall, "flag": False, "evidence": {}} if fall is not None else None}


def test_r_is_the_highest_score_over_its_own_threshold():
    sc = detector_scores(res(vit=0.139, fall=0.6), T)
    assert sc["vitals"]["r"] == pytest.approx(0.5) and sc["fall"]["r"] == pytest.approx(0.75)
    assert nearest(sc) == (pytest.approx(0.75), "fall")
    assert nearest({}) == (0.0, None)


def test_degraded_monitoring_with_the_fixed_rule_counts_as_at_threshold():
    sc = detector_scores(res(degraded=True, fixed=True), T)
    assert sc["vitals"]["r"] == 1.0 and "fall" not in sc


def test_no_rule_and_r_below_theta_is_triage_and_template_report_only():
    p = plan_team({"status": "NORMAL"}, res(vit=0.1), {"theta": 0.8}, T)
    assert p.active_ids == ["triage", "report"] and not p.watch and p.max_llm_requests == 0
    assert p.needs["r"] < p.thresholds["theta"] and p.closest and "% of its alert threshold" in p.closest


def test_near_miss_calls_the_investigator_only():
    p = plan_team({"status": "NORMAL"}, res(vit=0.25), {"theta": 0.8}, T)       # r = 0.25 / 0.278 = 0.90 > 0.8
    assert p.watch and p.active_ids == ["triage", "investigator", "report"] and p.max_llm_requests == 0
    assert {a.id: a.role for a in p.agents}["investigator"] == "watch"
    assert p.agents[1].need == pytest.approx(0.899, abs=1e-3) and p.agents[1].threshold == pytest.approx(0.8)


def test_the_cutoff_is_strict_and_comes_from_config_by_default():
    at = plan_team({"status": "NORMAL"}, res(vit=0.139), {"theta": 0.5}, T)                # r exactly 0.5: not above the cutoff
    assert not at.watch
    cfg_theta = PL.theta()
    assert 0.0 < cfg_theta < 1.0                                                            # shipped config carries the budget-chosen cutoff
    assert PL.theta({"theta": 0.3}) == 0.3 and PL.DEFAULT_THETA == 1.0


@pytest.mark.parametrize("status,want", [("MONITOR", {"triage", "investigator", "coordinator", "report"}), ("URGENT", {"triage", "investigator", "coordinator", "report"})])
def test_a_fired_rule_is_hard_evidence_and_needs_no_cutoff(status, want):
    for th in (0.0, 1.0, 1e9):
        p = plan_team({"status": status, "urgent_types": ["fall"] if status == "URGENT" else []}, None, {"theta": th})
        assert set(p.active_ids) == want and not p.watch and p.max_llm_requests == 5


@pytest.mark.parametrize("th", [0.0, 1.0, 1e9])
def test_urgent_always_has_an_owner_whatever_the_cutoff(th):
    p = plan_team({"status": "URGENT", "urgent_types": ["critical_vitals"], "reasons": ["critical vitals"]}, None, {"theta": th})
    assert p.has("investigator") and {a.id: a.role for a in p.agents}["coordinator"] == "owner"


def test_changed_day_coordinator_is_passive_and_data_gap_is_said():
    p = plan_team({"status": "MONITOR", "data_gap": True, "urgent_types": []}, res(vit=0.9))
    assert {a.id: a.role for a in p.agents}["coordinator"] == "passive" and "data gap" in {a.id: a.reason for a in p.agents}["investigator"]
    assert isinstance(p.agents[1], AgentPick) and p.agents[1].threshold is None          # decided by the rule, not the cutoff
