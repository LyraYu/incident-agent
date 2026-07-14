"""Sample tests: the five official test cases (TC001-TC005) from the dataset.

Each test feeds a fake loop (a canned model report + tool trace built from the
real tools) so the checks exercise the agent's own logic — verdict routing,
report assembly, cross-check, clarification — without calling the live model.

The canned reports are minimal cross_check-clean reports: every triggered
rule with its contact email, plus the incident's own facts. They double as
documentation of what the cross-check requires.

The final section pins the safety net itself: the cross-check must flag a
report that omits a triggered rule (with the exact corrective line), and the
reflection pass must repair it.
"""

from src.agent import cross_check, follow_up, investigate
from src.llm_client import execute_tool
from src.tools import check_escalation


def _eq_step(identifier):
    payload = execute_tool("get_equipment_details", {"identifier": identifier})
    return {"tool": "get_equipment_details", "args": {"identifier": identifier},
            "ok": True, "payload": payload}


def _esc_step(**facts):
    # The model calls check_escalation inside the loop; this step keeps the
    # fake trace realistic. The authoritative verdict is recomputed by code
    # from the locked incident record, so this step drives no assertion.
    payload = execute_tool("check_escalation", facts)
    return {"tool": "check_escalation", "args": facts, "ok": True, "payload": payload}


def _loop(report, trace):
    return lambda system, user: (report, trace)


def _triggered(result):
    return {c.rule_id for c in result.escalation.rule_checks if c.triggered}


# --- The five official test cases -----------------------------------------


def test_tc001_full_escalation():
    trace = [_eq_step("Etcher-03"),
             _esc_step(equipment_id="EQ001", alarm_code="RF101",
                       incident_timestamp="2026-06-22 10:35",
                       downtime_minutes=45, affected_lot="LOT1055")]
    report = ("# Incident Report INC001 — RF101 on EQ001, lot LOT1055. "
              "R001 david.koh@example.com; R002 irene.chua@example.com; "
              "R003 clara.wong@example.com; R004 vendor.support@example.com; "
              "R005 emily.ng@example.com. History H101, H102, H103. SOP001.")
    r = investigate("Etcher-03 triggered RF Power Instability...",
                    loop_fn=_loop(report, trace))
    assert r.status == "report"
    assert r.escalation.incident_timestamp.startswith("2026-06-22 10:35")
    assert _triggered(r) == {"R001", "R002", "R003", "R004", "R005"}
    assert r.escalation.requires_escalation
    assert r.cross_check_issues == []


def test_tc002_infer_alarm_no_over_escalation():
    trace = [_eq_step("CMP-02"),
             _esc_step(equipment_id="EQ002", alarm_code="CMP205",
                       incident_timestamp="2026-06-22 13:10",
                       downtime_minutes=18, affected_lot="LOT1056")]
    report = ("# Incident Report INC002 — CMP205 (Pad Pressure Low) on EQ002, "
              "lot LOT1056. R005 notify emily.ng@example.com. SOP004.")
    r = investigate("CMP-02 pressure alarm...", loop_fn=_loop(report, trace))
    assert r.status == "report"
    assert r.escalation.alarm_code == "CMP205"
    assert r.escalation.incident_timestamp.startswith("2026-06-22 13:10")
    assert _triggered(r) == {"R005"}
    assert not r.escalation.requires_escalation
    assert r.cross_check_issues == []


def test_tc003_escalate_severity_and_downtime():
    trace = [_eq_step("CVD-05"),
             _esc_step(equipment_id="EQ003", alarm_code="GAS012",
                       incident_timestamp="2026-06-22 14:05",
                       downtime_minutes=35, affected_lot="LOT1057")]
    report = ("# Incident Report INC003 — GAS012 on EQ003, lot LOT1057. "
              "R001 david.koh@example.com; R003 clara.wong@example.com; "
              "R005 emily.ng@example.com. SOP006.")
    r = investigate("CVD-05 has gas flow deviation...", loop_fn=_loop(report, trace))
    assert r.status == "report"
    assert r.escalation.alarm_code == "GAS012"
    assert _triggered(r) == {"R001", "R003", "R005"}
    assert r.escalation.requires_escalation
    assert r.cross_check_issues == []


def test_tc004_avoid_over_escalation():
    trace = [_eq_step("Litho-01"),
             _esc_step(equipment_id="EQ004", alarm_code="ALIGN011",
                       incident_timestamp="2026-06-22 15:20",
                       downtime_minutes=12, affected_lot="LOT1058")]
    report = ("# Incident Report INC004 — ALIGN011 (Low severity) on EQ004, "
              "lot LOT1058. R005 notify emily.ng@example.com. SOP008.")
    r = investigate("Litho-01 alignment failure...", loop_fn=_loop(report, trace))
    assert r.status == "report"
    assert _triggered(r) == {"R005"}
    assert not r.escalation.requires_escalation
    assert r.cross_check_issues == []


def test_tc005_missing_equipment_clarifies():
    trace = [_eq_step("ALPHA-99")]
    report = "Equipment 'ALPHA-99' is not in the master list; please verify the name."
    r = investigate("Unknown tool ALPHA-99 has alarm ZX999.",
                    loop_fn=_loop(report, trace))
    assert r.status == "needs_clarification"
    assert "ALPHA-99" in r.clarification


# --- Guardrail: the safety net itself --------------------------------------


def test_cross_check_flags_missing_triggered_rule_with_exact_fix():
    """A report omitting a triggered rule must be flagged, and the issue text
    must carry the exact corrective line (so reflection can apply it)."""
    verdict = check_escalation(equipment_id="EQ004", alarm_code="ALIGN011",
                               incident_timestamp="2026-06-22 15:20",
                               downtime_minutes=12, affected_lot="LOT1058")
    incident = {"incident_id": "INC004", "alarm_code": "ALIGN011",
                "affected_lot": "LOT1058"}
    bad = ("# Incident Report INC004 — ALIGN011 on EQ004, lot LOT1058. "
           "Escalation List: None.")
    issues = cross_check(bad, verdict, incident)
    assert any("R005" in i for i in issues)
    joined = " ".join(issues)
    assert "Emily Ng" in joined and "emily.ng@example.com" in joined


def test_reflection_repairs_flagged_report():
    """When the cross-check flags the first report, the reflection pass runs
    once with the issues in its prompt, and the fixed report is adopted."""
    trace = [_eq_step("Litho-01"),
             _esc_step(equipment_id="EQ004", alarm_code="ALIGN011",
                       incident_timestamp="2026-06-22 15:20",
                       downtime_minutes=12, affected_lot="LOT1058")]
    bad = ("# Incident Report INC004 — ALIGN011 on EQ004, lot LOT1058. "
           "Escalation List: None.")
    good = ("# Incident Report INC004 — ALIGN011 on EQ004, lot LOT1058. "
            "R005 Manufacturing Supervisor (Emily Ng, ENG005, "
            "emily.ng@example.com).")
    calls = []

    def loop(system, user):
        calls.append(system)
        return (bad, list(trace)) if len(calls) == 1 else (good, [])

    r = investigate("Litho-01 alignment failure...", loop_fn=loop)
    assert len(calls) == 2                 # reflection ran exactly once
    assert "R005" in calls[1]              # the fix prompt named the issue
    assert "Emily Ng" in r.report          # the repaired report was adopted
    assert r.cross_check_issues == []


def test_unknown_alarm_on_known_equipment_clarifies():
    trace = [_eq_step("Etcher-03")]   # equipment found, incident locked
    report = ("## Summary — what could not be found.\n"
              "Alarm XYZ888 is not a known alarm code.\n"
              "## Recommendation — ask the user to verify the alarm code.")
    r = investigate("Etcher-03 reports alarm XYZ888.", loop_fn=_loop(report, trace))
    assert r.status == "needs_clarification"
    assert "XYZ888" in r.clarification


def test_minimal_input_recovers_from_record():
    trace = [_eq_step("Etcher-03")]
    report = ("# Incident Report INC001 — RF101 on EQ001, lot LOT1055. "
              "R001 david.koh@example.com; R002 irene.chua@example.com; "
              "R003 clara.wong@example.com; R004 vendor.support@example.com; "
              "R005 emily.ng@example.com. H101 H102 H103. SOP001.")
    r = investigate("Etcher-03 is down.", loop_fn=_loop(report, trace))
    assert r.status == "report"
    assert _triggered(r) == {"R001", "R002", "R003", "R004", "R005"}
    assert r.cross_check_issues == []


def test_follow_up_scans_answer_for_fabricated_ids():
    ok, issues = follow_up("what fixed it?", history=[],
                           loop_fn=lambda s, u: ("H102 was fixed by tightening the RF cable.", []))
    assert issues == []
    bad, issues = follow_up("what fixed it?", history=[],
                            loop_fn=lambda s, u: ("See incident H999 for details.", []))
    assert issues and "H999" in issues[0]