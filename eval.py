"""
Live evaluation: run every test case against the real model and score the
results deterministically.

Cases: the official five from the dataset's test_cases sheet (inputs read
from the sheet, never retyped) plus three additional scenarios covering
assignment requirements the official cases leave untouched — an unknown
alarm on a known equipment, a minimal-information report recovered
entirely from the incident record, and a known equipment with no open
incident.

Scored per run: result status, exact set of triggered escalation rules,
required citations present, shipped cross-check issues, fabricated ids,
and whether the reflection pass self-repaired the report. Prose quality is
out of scope (see the design document's limitations).

Usage:
    python eval.py                # one run per case
    python eval.py --runs 3      # stability check
    python eval.py --out FILE    # default: eval_results.md
"""

import argparse
import sys

from src.agent import investigate
from src.data_loader import get_sheets

# Deterministic expectations per official case (verdicts verified against the
# dataset during batch testing; see design document section 6).
EXPECT = {
    "TC001": {"status": "report",
              "rules": {"R001", "R002", "R003", "R004", "R005"}},
    "TC002": {"status": "report", "rules": {"R005"}},
    "TC003": {"status": "report", "rules": {"R001", "R003", "R005"}},
    "TC004": {"status": "report", "rules": {"R005"}},
    "TC005": {"status": "needs_clarification"},
}

# Ids the sheet's expected_behaviour names explicitly; the report must cite them.
MUST_CITE = {
    "TC001": {"EQ001", "RF101", "H101", "H102", "H103", "SOP001"},
    "TC002": {"CMP205"},
    "TC003": {"GAS012", "SOP006"},
    "TC004": {"SOP008"},
    "CUST-A": {"XYZ888", "RF101"},
}

# Additional scenarios (not in the sheet): the assignment's "Unknown alarm"
# and "Missing information" requirements head-on, plus the third clarification
# path (a known equipment with no open incident).
CUSTOM_CASES = [
    ("CUST-A",
     "Etcher-03 reports alarm XYZ888, downtime 20 minutes.",
     {"status": "report",
      "rules": {"R001", "R002", "R003", "R004", "R005"}},
     "unknown alarm code on a known equipment -> corrected from the "
     "incident record with an explicit note, not guessed"),
    ("CUST-B",
     "Etcher-03 is down.",
     {"status": "report",
      "rules": {"R001", "R002", "R003", "R004", "R005"}},
     "minimal information -> all facts recovered from the incident record"),
    ("CUST-C",
     "Diffusion-02 pump making abnormal noise, please investigate.",
     {"status": "needs_clarification"},
     "known equipment without an open incident -> ask for incident details"),
]


def load_cases():
    sheet = get_sheets()["test_cases"]
    cases = [(r["test_case_id"], r["input_text"], EXPECT[r["test_case_id"]],
              r["expected_behaviour"])
             for _, r in sheet.iterrows()]
    return cases + CUSTOM_CASES


def score_run(case_id, result, expect):
    status_ok = result.status == expect["status"]
    if "rules" in expect:
        triggered = ({c.rule_id for c in result.escalation.rule_checks
                      if c.triggered} if result.escalation else set())
        escalation_ok = triggered == expect["rules"]
    else:
        escalation_ok = None  # clarification cases have no verdict
    cite = MUST_CITE.get(case_id)
    if cite and result.report:
        cited_ok = all(token in result.report.upper() for token in cite)
    else:
        cited_ok = None
    issues = result.cross_check_issues or []
    fabricated = sum(1 for i in issues if "not in the dataset" in i)
    repaired = getattr(result, "reflection_used", False)
    passed = (status_ok and escalation_ok in (True, None)
              and cited_ok in (True, None) and not issues)
    return {"status_ok": status_ok, "escalation_ok": escalation_ok,
            "cited_ok": cited_ok, "issues": len(issues),
            "fabricated": fabricated, "repaired": repaired, "pass": passed}


def fmt(value):
    return {True: "yes", False: "NO", None: "-"}.get(value, str(value))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", default="eval_results.md")
    args = parser.parse_args()

    lines = ["# Evaluation results", "",
             f"{args.runs} run(s) per case, live model, deterministic scoring.",
             "",
             "| case | run | status ok | escalation exact | cites ok "
             "| shipped issues | fabricated ids | self-repaired | pass |",
             "|---|---|---|---|---|---|---|---|---|"]
    total = failed = 0

    for case_id, text, expect, note in load_cases():
        for run in range(1, args.runs + 1):
            print(f"[{case_id} run {run}] {text[:60]}...", flush=True)
            row = score_run(case_id, investigate(text), expect)
            total += 1
            failed += 0 if row["pass"] else 1
            lines.append(
                f"| {case_id} | {run} | {fmt(row['status_ok'])} "
                f"| {fmt(row['escalation_ok'])} | {fmt(row['cited_ok'])} "
                f"| {row['issues']} | {row['fabricated']} "
                f"| {'yes' if row['repaired'] else '-'} | {fmt(row['pass'])} |")
        lines.append(f"| _{case_id}: {note}_ |||||||||")

    lines += ["", f"**{total - failed}/{total} passed.**"]
    report = "\n".join(lines)
    print("\n" + report)
    with open(args.out, "w") as f:
        f.write(report + "\n")
    print(f"\nwritten to {args.out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()