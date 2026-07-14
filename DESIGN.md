# Design Document — Incident Investigation Agentic AI Assistant

This system helps equipment engineers investigate machine downtime. It takes a
free-text incident description, gathers evidence from the plant dataset
through tool calls, and produces a structured investigation report with an
escalation verdict.

One principle drives the design: **the model investigates and writes; code
owns everything that must be correct.** The escalation verdict, the factual
gate on the report, and the repair loop are all deterministic. Section 6
presents the test data behind this claim.

## 1. Solution architecture

```
 free-text incident description
            │
            ▼
 ┌─ agent loop (Gemini, ≤12 rounds) ──────────────────────────┐
 │  identify equipment → resolve alarm → gather evidence      │
 │  → check_escalation → write report                         │
 │  (6 read tools + rule engine; misses return found:false)   │
 └─────────────────┬───────────────────────────────────────────┘
                   │  report text + tool trace
                   ▼
      lock the incident record from the trace          [code]
                   ▼
      authoritative verdict = check_escalation(record) [code]
                   ▼
      cross-check: ids, rules, contacts, facts         [code]
                   ▼
      issues found? → one reflection pass
                      adopt only if strictly better    [code]
                   ▼
      InvestigationResult { report, verdict, issues, trace }
```

| Module | Responsibility |
|---|---|
| `agent.py` | investigation prompt, orchestration, cross-check, reflection |
| `tools.py` | 7 dataset tools, including the deterministic escalation rule engine |
| `llm_client.py` | Gemini client, tool declarations, manual tool loop, retry |
| `models.py` | pydantic models for tool outputs, verdict, and the final result |
| `api.py` | FastAPI shell over `investigate()` |

The CLI and the REST API are both thin shells over the same `investigate()`
function, so every test result applies to both interfaces. The service also
ships with a Dockerfile for one-command startup; the venv path remains the
development workflow.

## 2. Agent workflow and orchestration

The model runs the whole investigation in a single tool loop. Its system
prompt fixes a numbered procedure: identify the equipment, resolve the alarm,
gather evidence (similar incidents, maintenance, sensor readings, SOP), call
`check_escalation`, and write the report in a fixed layout. Keeping
everything in one loop means the evidence stays in the model's context while
it writes — the report is grounded in tool results the model has just seen,
and each investigation costs exactly one model conversation.

After the loop, code takes over in four steps:

1. **Lock the incident.** The equipment lookup returns the equipment together
   with its open incident. That record — id, timestamp, downtime, affected
   lot — is read from the trace and becomes the single source of truth.
2. **Compute the verdict.** Code calls the rule engine with the five inputs
   taken from the locked record. The model also calls `check_escalation`
   inside the loop (it needs the result to write the escalation list), but
   that call only informs its writing; the authoritative verdict never
   depends on the model transcribing parameters correctly.
3. **Cross-check the report** against the dataset and the verdict
   (section 5, decision B).
4. **Reflect once if needed.** If the cross-check finds issues, the model is
   asked to fix exactly those issues. The revision is adopted only if it is
   non-empty and strictly reduces the issue count; otherwise the original
   ships with its issues visible in `cross_check_issues`.

Two kinds of "unknown" get two different treatments. An unknown equipment
blocks the investigation — the pipeline stops before any verdict and returns
a clarification request (TC005). An unknown alarm code on a known equipment
does not: the incident record already holds the real code, so the
investigation proceeds on the record and the report states the correction
explicitly, naming both the code the user gave and the code the record shows
(evaluation case CUST-A). Only when there is no incident record to fall back
on does an unidentified alarm produce a clarification request.

A finished investigation can be questioned. `follow_up()` continues the same
model conversation (the tool loop keeps its history when given one), with the
read tools available and the answer passed through the same fabricated-id
scan as the report. The reflection pass deliberately runs outside this
history, so repair chatter never pollutes the follow-up context. The REST API
exposes this as a `session_id` on each investigation and a `/follow_up`
endpoint; the CLI as an interactive `chat` mode. Sessions are held in
process memory — a production deployment would externalize the store and add
expiry.

The tool trace records every call the model made, so a reviewer can replay
exactly how the evidence was gathered.

## 3. Tool design

| Tool | Purpose |
|---|---|
| `get_equipment_details` | equipment master data **plus its open incident** |
| `get_alarm_details` | one alarm by code/description, or all alarms for a tool type |
| `get_similar_incidents` | same equipment + same alarm, newest first (max 5) |
| `get_maintenance_history` | recent maintenance for one equipment (max 5) |
| `get_sensor_readings` | sensor time series for one incident |
| `get_sop` | troubleshooting steps for an alarm code |
| `check_escalation` | deterministic rule engine → full verdict |

Three designs carry most of the weight:

**The incident travels with the equipment.** `get_equipment_details` joins
the open incident into its result, so one call establishes every fact the
investigation depends on: the real incident id, timestamp, downtime, and
affected lot. The model never assembles these from separate lookups, and the
orchestrator reads the verdict inputs from this same record.

**Alarm lookup has a precise mode and an explore mode.** Passing a code or an
exact description returns one alarm; passing a tool type lists that type's
alarms so the model can match vague wording ("pressure alarm") against real
candidates instead of guessing a code (TC002).

**The rule engine trusts nothing it can recompute.** `check_escalation`
recomputes recurrence counts directly from `incident_history` rather than
accepting counts from the caller, resolves equipment and alarm names to ids
before matching so a name/id mismatch cannot silently produce zero matches,
and returns every rule check — triggered or not — with the observed facts it
was judged on.

At the loop boundary, a missing record becomes `{"found": false, ...}` and a
malformed call becomes an error payload the model can react to. A bad tool
call never crashes the loop.

## 4. Prompting strategy

The system prompt has three parts: a numbered procedure, an exact report
layout, and grounding rules.

- **Procedure over persona.** The prompt spends its budget on ordered steps
  and extraction targets, not on role descriptions. Each step names the tool
  to use and what to carry forward.
- **Exact output shapes.** The escalation list format is specified down to
  the line: role, name, engineer id, email copied verbatim from the tool
  result. If no rule triggered, the model must write exactly `None` and is
  forbidden from discussing untriggered rules. Constraining the shape also
  removes the degrees of freedom in which fabrication happens — a model that
  may only echo tool data or write `None` has nowhere to invent a contact.
- **Records beat the user.** If the user's account conflicts with the data
  (a different time, a non-existent alarm code, or "twice last week" against
  three recorded occurrences), the prompt requires an explicit correction
  using the records.
- **Ambiguity is removed at the data source, not policed downstream.** The
  rule engine's recurrence counts are self-labeling — its output strings say
  "including the current incident" — so any count the model quotes carries
  its own frame of reference. This retired a whole class of "3 vs 4"
  wording drift without adding a single check.
- **Repair instructions carry the answer.** When the cross-check flags a
  missing escalation line, the issue text includes the exact corrective line.
  The reflection pass then performs a mechanical substitution instead of a
  new investigation — in testing this took the repair rate from partial to
  100% (section 6).

## 5. Key implementation decisions and assumptions

**A. The escalation verdict is computed by code from the locked incident
record.** Escalation is a policy decision over facts held in the system of
record; a probabilistic component adds risk and no value on that path.
Stress testing made the risk concrete: under a deliberately weak model
(`gemini-3.1-flash-lite`), the model's own `check_escalation` call omitted
the `affected_lot` parameter in roughly half of all runs — which, if trusted,
silently drops a notification rule. With the verdict computed from the
record, the same fault is caught by the cross-check and repaired.

**B. The cross-check tests only determinable faults.** Four checks, each with
a ground truth: (1) every id cited in the report exists in the dataset — the
id pattern is derived from the data, not hand-listed, so new id families are
covered automatically; (2) every triggered rule appears in the report;
(3) each cited rule carries the correct contact email; (4) the incident's own
id, alarm code, and lot appear. Prose wording is deliberately out of scope
(see Limitations).

**C. Reflection is bounded and verified.** One pass, fix-only-what-was-
flagged, adopted only if the issue count strictly decreases. This keeps the
worst-case cost at two model conversations and makes failure honest: a report
that could not be repaired ships with its issues visible rather than being
retried indefinitely.

**D. Model choice is a configuration, not a dependency.** The submission
default is `gemini-3-flash-preview` (thinking level `low`). The same code was
stress-tested on the weaker `gemini-3.1-flash-lite` with zero silent errors
(section 6); switching models is a two-line change in `config.py`. For
production, this means the model tier can be chosen on cost alone.

**E. Follow-up memory reuses the investigation's own conversation.** Rather
than a separate memory subsystem, the tool loop simply keeps its message
history when handed a list, and `follow_up()` continues it. One design rule
protects the deliverable: the report and verdict are final — follow-ups may
query and explain, and the prompt instructs the model to decline requests to
revise the verdict, pointing to the rule engine. Answers pass the same
fabricated-id scan as reports.

**Enhancements considered and declined.** Vector search: the dataset is
small and fully structured; exact lookups are already correct, and embedding
retrieval on thirteen small sheets would add an approximation layer where
none is needed. Its entry condition is stated in the roadmap below.

**Production roadmap.** The natural next step is closing the knowledge loop:
when an investigation is resolved, write the confirmed root cause and
corrective action back into `incident_history` — drafted by the agent,
committed only after an engineer signs off. The write path would mirror the
system's core rule (the model drafts, deterministic gates guard, a human
approves) so the knowledge base cannot be polluted by an unreviewed model
output. Once that loop accumulates history at scale, two deferred items earn
their place: semantic search over past investigations (engineers asking "how
was that vibration issue fixed" in natural language), and an externalized
session store with expiry.

**Assumptions**

- `current_incidents` is the system of record. User-supplied values that
  conflict with it are corrected in the report, not adopted.
- Some maintenance records post-date the incidents (e.g. an inspection dated
  2026-06-25 against incidents on 2026-06-22). The tools return them as-is;
  no artificial "today" cutoff is imposed.
- `lot_wip` is not used: it conflicts with `current_incidents` (e.g. LOT1055
  is on EQ001 in `current_incidents` but on EQ005/Hold in `lot_wip`), and the
  incident record is the authoritative source for lot impact.
- The prompt anchors the investigation "around 2026-06-22", matching the
  dataset's incident cluster, without treating it as a hard clock.

**Limitations**

- The cross-check guards identifiers, escalation facts, and the verdict. It
  does not parse prose semantics: in batch logs the model occasionally
  attaches a wrong time window to a list of (real) incident ids (e.g.
  counting a 7-week-old incident inside "the last 30 days"), slips a year in
  a date (2024 for 2026), or over-reads in-range sensor noise as a signal.
  Observed in well under 10% of runs, never affecting the verdict or the
  recommended actions; prose-level semantic checks were judged not worth the
  complexity.
- The fabricated-id scan only recognizes id prefixes that exist in the
  dataset; an invented id with a novel prefix passes the scan (it is still
  caught by the contact-email check when it appears in an escalation line).
  Symmetrically, a user-supplied wrong code that reuses a known prefix (say
  RF999) would be flagged by the scan even when the report cites it only to
  correct it — a visible false alarm, never a silent one.

## 6. Testing and reliability evidence

**Offline tests** (`pytest -q`, no API key, ~4 s): ten tests — the five
official cases asserted on verdict rule sets, escalation semantics, and a
clean cross-check; the two additional scenarios (unknown alarm, minimal
input); a follow-up answer scan; and two guardrail tests that pin the safety
net itself — a report missing a triggered rule must be flagged with the
exact corrective line, and the reflection pass must repair it.

**Live evaluation** (`python eval.py`): every case — official five plus the
two additional scenarios — run against the real model and scored
deterministically (status, exact escalation rule set, required citations,
shipped issues, fabricated ids). The committed `eval_results.md` shows
**14/14 passed** at two runs per case; a reviewer can regenerate it with one
command.

**Stress batches.** The same inputs were also run repeatedly against two
model tiers with identical code and prompts:

| | `gemini-3.1-flash-lite` (stress) | `gemini-3-flash-preview` (default) |
|---|---|---|
| runs | 30+ (TC004 focus) | 50+ (all cases) |
| model omits a verdict parameter | ~50% of runs | 0 |
| silent wrong reports | **0** | 0 |
| final report correct | 100% | 100% |

The lite runs are the point: even with the model dropping a required fact in
half of all runs, no error shipped silently — every fault was either
repaired by the reflection pass on the spot or visible in
`cross_check_issues`. Under the submission model the safety net is almost
entirely idle.

Live outputs were verified field-by-field against the dataset (sensor values,
engineer ids and emails, history ids and dates, maintenance records, SOP
steps) during batch review.