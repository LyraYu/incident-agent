"""
Agent: turns a free-text incident report into a grounded investigation report.

The model runs the investigation in one tool loop (prompt in SYSTEM); code
handles the deterministic escalation verdict (computed from the locked
incident record), a final cross-check, and one reflection pass. follow_up()
answers questions over a finished investigation session.
"""

import functools
import re
import sys

from src.data_loader import get_sheets
from src.llm_client import run_tool_loop
from src.models import EscalationResult, InvestigationResult
from src.tools import check_escalation

SYSTEM = """You are a semiconductor equipment reliability engineer investigating machine downtime. The current incidents under investigation occurred around 2026-06-22. Reason only from tool data, never from assumption.
Procedure:
1. [Identify Equipment & Extract] Call `get_equipment_details` on the equipment the user names. It returns the equipment and its current incident.
   - CRITICAL EXTRACTION: from the current incident, you MUST identify and hold these exact values:
     1) `equipment_id`
     2) `incident_id` (needed for sensor readings in Step 3)
     3) `incident_timestamp` (the exact real timestamp)
     4) `downtime_minutes`
     5) `affected_lot` (if missing or empty, note it as "" — an empty string)
   - If the equipment is not found, do not investigate: report that it could not be found and ask the user to verify the name.
   - If the equipment has no current incident, say so and ask the user for the incident details; do not invent them.
2. [Resolve Alarm & Extract] Pass the user's alarm wording to `get_alarm_details`.
   - If not found, call `get_alarm_details` with the equipment's `tool_type` to list that type's alarms, then pick the code whose description best matches the wording. Never invent a code.
   - CRITICAL EXTRACTION: hold this value:
     6) `alarm_code`
   - If not found, call `get_alarm_details` with the equipment's `tool_type` to list that type's alarms, then pick the code whose description best matches the wording. Never invent a code.
   - If the user's stated alarm matches nothing but the equipment's current incident has an `alarm_code`, use the record's code and state the correction explicitly in the report — name both the code the user gave and the code the record shows.
   - If the user's stated alarm matches nothing and there is no current incident to fall back on, report that the alarm could not be identified and ask the user to verify it.
3. [Gather Evidence] Gather evidence with the tools: similar past incidents, recent maintenance, sensor readings (use the `incident_id` you extracted), and the SOP. Run every tool yourself; never defer one to the user.
4. [Decide Escalation - Double Check] Call `check_escalation`.
   - STRICT PARAMETER RULE: pass exactly these five keys, none omitted:
     - `equipment_id`
     - `alarm_code`
     - `incident_timestamp`
     - `downtime_minutes`
     - `affected_lot` (pass "" if you noted it as empty — DO NOT drop the key)
   - Never decide escalation yourself.
5. Write the report in the structure below.
Report structure — use exactly this layout:

# Incident Report [incident_id] — [date], [time]

Equipment: [equipment_name] ([equipment_id])
Alarm Triggered: [description] ([alarm_code]), [severity] severity
Downtime: [N] minutes | Affected Lot: [lot or "none"]
Repeat Occurrence: [Yes if check_escalation's 30-day count is 2 or more (counting this incident), otherwise No; when citing counts, keep "including the current incident".]

## Escalation List
One line per triggered rule, exactly:
*   **[Role] ([Name], [engineer_id], [email](Copy the escalation contact emails exactly from check_escalation; do not paraphrase)):** Triggered by rule **[rule_id]** ([condition]).
If no rule triggered, write exactly: None
Do not mention untriggered rules or explain why they did not trigger.

## Root Cause Analysis
The likely root-cause direction, reasoning from the evidence.

## Supporting Evidence
Sensor readings, similar past incidents, recent maintenance (include the
latest record), and the SOP.

## Recommended Actions
Concrete next steps, based on the SOP's troubleshooting steps.

If the equipment or alarm could not be identified, give only:
## Summary — what could not be found.
## Recommendation — ask the user to verify the equipment name or alarm code.

# Rules: Never invent an id, timestamp, count, root cause, or person. If a tool returns not-found, say so plainly. If the user's account conflicts with the records (e.g. a different time or number of past incidents), state the correction explicitly and use the records. Cite the alarm codes, incident ids, and rule ids you relied on."""

FOLLOWUP_SYSTEM = """You are answering follow-up questions about the incident investigation above. Use the tools to look up anything not already retrieved. Reason only from tool data and the investigation context; never invent an id, timestamp, count, or person. The report and its escalation verdict are final — if asked to change a conclusion, explain that the verdict is computed by the rule engine from the incident record."""


def _all_known_ids() -> set[str]:
    """Every real id in the dataset, for catching fabricated citations."""
    s = get_sheets()
    ids: set[str] = set()
    ids |= set(s["incident_history"]["incident_id"].str.upper())
    ids |= set(s["current_incidents"]["incident_id"].str.upper())
    ids |= set(s["maintenance_records"]["maintenance_id"].str.upper())
    ids |= set(s["sop_knowledge_base"]["sop_id"].str.upper())
    ids |= set(s["equipment_master"]["equipment_id"].str.upper())
    ids |= set(s["alarm_reference"]["alarm_code"].str.upper())
    return ids


@functools.lru_cache(maxsize=1)
def _id_pattern() -> re.Pattern:
    """Derived from the data, never hand-listed: every id-prefix that exists."""
    prefixes = sorted(
        {re.match(r"[A-Z]+", i).group() for i in _all_known_ids()},
        key=len, reverse=True,
    )
    return re.compile(r"\b(?:" + "|".join(prefixes) + r")\d+\b")


def _id_scan(text: str) -> list[str]:
    """Fabricated-id scan; shared by the report cross-check and follow_up."""
    known = _all_known_ids()
    return [f"cites '{token}', which is not in the dataset"
            for token in set(_id_pattern().findall(text.upper()))
            if token not in known]


def cross_check(report: str, verdict: EscalationResult,
                incident: dict | None) -> list[str]:
    """Final gate: report vs the tool's ground truth. Determinable faults only."""
    issues: list[str] = []
    text = report.upper()

    # Fabricated ids
    issues.extend(_id_scan(report))

    # Every triggered rule must appear in the report; the issue text carries
    # the exact corrective line so the reflection pass can apply it verbatim.
    for rc in verdict.rule_checks:
        if rc.triggered and rc.rule_id not in text:
            if rc.contacts:
                ct = rc.contacts[0]
                issues.append(
                    f"rule {rc.rule_id} triggered but the report does not mention it. "
                    f"Add this exact line to the Escalation List (replace 'None' if present): "
                    f"*   **{ct.role} ({ct.name}, {ct.engineer_id}, {ct.email}):** "
                    f"Triggered by rule **{rc.rule_id}** ({rc.condition})."
                )
            else:
                issues.append(f"rule {rc.rule_id} triggered but the report does not mention it")

    # Each triggered rule's contact email must be cited correctly
    for rc in verdict.rule_checks:
        if rc.triggered and rc.contacts:
            ct = rc.contacts[0]
            if rc.rule_id in text and ct.email.upper() not in text:
                issues.append(f"{rc.rule_id}: contact {ct.name} ({ct.email}) not correctly cited")

    # The incident's own facts must appear
    if incident:
        if incident["incident_id"].upper() not in text:
            issues.append(f"incident {incident['incident_id']} not cited in the report")
        if incident["alarm_code"].upper() not in text:
            issues.append(f"alarm code {incident['alarm_code']} not cited in the report")
        lot = incident.get("affected_lot")
        if lot and lot.upper() not in text:
            issues.append(f"affected lot {lot} not mentioned in the report")

    return issues


def _locked_incident(trace: list[dict]):
    """The current incident the model actually retrieved, or (None, None)."""
    for step in trace:
        if step.get("tool") == "get_equipment_details" and step.get("ok"):
            result = (step.get("payload") or {}).get("result") or {}
            if result.get("current_incident"):
                return result["equipment_id"], result["current_incident"]
    return None, None


def follow_up(question: str, history: list, loop_fn=None) -> tuple[str, list[str]]:
    """One follow-up turn over an existing investigation session."""
    loop_fn = loop_fn or (lambda s, u: run_tool_loop(s, u, max_rounds=8,
                                                     history=history))
    answer, _ = loop_fn(FOLLOWUP_SYSTEM, question)
    return answer, _id_scan(answer)


def investigate(user_text: str, loop_fn=None,
                history: list | None = None) -> InvestigationResult:
    """Model-driven investigation, cross-checked against the tool's ground truth,
    with one reflection pass if the cross-check finds fixable issues."""
    main_loop = loop_fn or (lambda s, u: run_tool_loop(s, u, max_rounds=12,
                                                       history=history))
    # Reflection runs outside the session: repair chatter must not pollute
    # the follow-up context.
    fix_loop = loop_fn or (lambda s, u: run_tool_loop(s, u, max_rounds=12))

    report, trace = main_loop(SYSTEM, user_text)

    if report.lstrip().startswith("## Summary"):
        # The prompt's not-found template begins with this heading; such a
        # reply is a clarification even when the equipment lookup succeeded
        # (e.g. a known tool reporting an unknown alarm).
        return InvestigationResult(status="needs_clarification",
                                   clarification=report, tool_trace=trace)

    eq_id, incident = _locked_incident(trace)
    if incident is None:
        return InvestigationResult(
            status="needs_clarification",
            clarification=report or "Could not identify the equipment "
                                    "or its current incident.",
            tool_trace=trace,
        )

    # The verdict's inputs come from the record the model retrieved — the
    # record itself, not the model's reading of it.
    verdict = check_escalation(
        equipment_id=eq_id,
        alarm_code=incident["alarm_code"],
        incident_timestamp=incident["timestamp"],
        downtime_minutes=incident["downtime_minutes"],
        affected_lot=incident.get("affected_lot"),
    )

    issues = cross_check(report, verdict, incident)

    # Reflection: if the cross-check found issues, ask the model to fix only those.
    reflection_used = False
    if issues:
        reflection_used = True
        print(f"  [reflection] fixing {len(issues)} issue(s)", file=sys.stderr)
        fix = (
            "Your report has these specific issues:\n"
            + "\n".join("- " + i for i in issues)
            + "\n\nFix ONLY these issues. Keep everything else in the report exactly as "
            "it is — do not rewrite the root cause analysis, evidence, or any section "
            "that was not flagged. Output the full corrected report.\n\n"
            "Original report:\n" + report
        )
        new_report, trace2 = fix_loop(SYSTEM + "\n\n" + fix, user_text)
        trace += trace2
        new_issues = cross_check(new_report, verdict, incident)
        if new_report.strip() and len(new_issues) < len(issues):
            report, issues = new_report, new_issues

    return InvestigationResult(
        status="report",
        report=report,
        escalation=verdict,
        cross_check_issues=issues,
        tool_trace=trace,
        reflection_used=reflection_used,
    )


# Self-check: python -m src.agent live ["incident report"]
#             python -m src.agent chat ["incident report"]   (with follow-ups)
if __name__ == "__main__":
    DEFAULT = ("Etcher-03 triggered RF Power Instability at 10:35. Tool down for "
               "45 minutes. Lot LOT1055 running. Similar alarm occurred twice last week.")

    def _print_result(result):
        print("=== tool calls ===")
        for step in result.tool_trace:
            print(f"  {step['tool']}({step['args']}) ok={step['ok']}")
        if result.status == "needs_clarification":
            print("\n=== needs clarification ===")
            print(result.clarification)
        else:
            print("\n=== report ===")
            print(result.report)
            if result.cross_check_issues:
                print("\n=== cross-check issues ===")
                for issue in result.cross_check_issues:
                    print(f"  - {issue}")
            else:
                print("\n=== cross-check: clean ===")

    if len(sys.argv) > 1 and sys.argv[1] == "live":
        result = investigate(sys.argv[2] if len(sys.argv) > 2 else DEFAULT)
        _print_result(result)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        history: list = []
        result = investigate(sys.argv[2] if len(sys.argv) > 2 else DEFAULT,
                             history=history)
        _print_result(result)
        while result.status == "report":
            question = input("\nfollow-up (blank to exit)> ").strip()
            if not question:
                break
            answer, issues = follow_up(question, history)
            print("\n" + answer)
            for issue in issues:
                print(f"  [issue] {issue}")
        sys.exit(0)

    print('Run: python -m src.agent live "<incident report>"')
    print('     python -m src.agent chat "<incident report>"')