# Incident Investigation Agentic AI Assistant

An agentic AI assistant that helps semiconductor equipment engineers
investigate machine downtime incidents. It accepts a free-text incident
description, retrieves evidence from the plant dataset through tool calls,
reasons over the context with a real LLM (Gemini), and produces a structured
investigation report with a deterministic escalation verdict. A finished
investigation can then be questioned in follow-up turns.

## Design in one paragraph

The LLM runs the whole investigation in a single tool loop — identifying the
equipment, resolving the alarm, gathering evidence, and writing the report.
Everything that must be correct is owned by code: the escalation verdict is
computed by a deterministic rule engine whose five inputs are read from the
incident record itself (never transcribed by the model); a cross-check
validates every id, escalation contact, and incident fact in the report
against the dataset; and a single reflection pass asks the model to fix only
the specific issues found. The result: even under a deliberately weak model,
errors are absent, auto-repaired, or explicitly visible in
`cross_check_issues` — never silent. Architecture, trade-offs, and
stress-test data are in the design document.

## Project structure

```
src/
  agent.py        # investigation prompt, orchestration, cross-check, reflection, follow_up
  tools.py        # 7 dataset tools, incl. the deterministic check_escalation rule engine
  llm_client.py   # Gemini client, tool declarations, manual tool loop, retry logic
  models.py       # typed (pydantic) structures: tool outputs, verdict, report
  data_loader.py  # cached read-only access to the Excel dataset
  config.py       # model + thinking-level configuration
  api.py          # FastAPI shell: investigate, follow-up sessions, report views
  ui.py           # engineer-facing web page (investigate + follow-up in one place)
tests/
  test_agent.py   # 12 offline tests (official cases, extra scenarios, guardrails)
eval.py           # live evaluation harness -> eval_results.md
eval_results.md   # committed evaluation matrix (default tier, 40/40)
eval_results_flashlite.md   # stress-tier matrix (36/40)
DESIGN.md         # design document (submitted as DESIGN.docx)
Dockerfile        # container packaging (one-command startup)
docs/
  sample_report_CMP-02.pdf   # a rendered report, printed from the HTML view
data/
  Incident_Investigation_dataset.xlsx
requirements.txt
```

## Setup

**Prerequisites:** Python 3.11+, a Google Gemini API key
(free at https://aistudio.google.com).

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_key_here
```

### Model configuration

Model selection lives in `src/config.py` and nowhere else:

```python
GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_THINKING_LEVEL = "low"   # Gemini 3.x only; set to None for flash-lite / 2.x
```

To switch models, change these two lines. The system was additionally
stress-tested on the weaker `gemini-3.1-flash-lite` with zero silent errors —
correctness does not depend on the model tier. See the design document for
the data.

## Running

### CLI

```bash
# one-shot investigation
python -m src.agent live "Etcher-03 triggered RF Power Instability at 10:35. Tool down for 45 minutes. Lot LOT1055 running. Similar alarm occurred twice last week."

# investigation + interactive follow-up questions
python -m src.agent chat "Etcher-03 is down."
```

The output shows the tool-call trace, the report, and the cross-check
result; `chat` then takes follow-up questions (e.g. "What fixed H102 last
time?") against the same session.

### REST API

```bash
uvicorn src.api:app --port 8000
```

Interactive docs at http://localhost:8000/docs; the engineer-facing web UI
is at http://localhost:8000/ui. Endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /investigate` | full structured result: report, verdict, cross-check, trace, `session_id` |
| `POST /follow_up` | ask a question about a previous session (`session_id` + `question`) |
| `POST /investigate/report` | plain-text report only |
| `GET /report/demo?text=...` | rendered HTML report, open directly in a browser |
| `GET /ui` | engineer-facing web page: describe an incident, read the report, ask follow-ups |
| `GET /health` | health check |

```bash
curl -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"CMP-02 pressure alarm. Downtime 18 minutes. Lot LOT1056.\"}"
```

Input is validated (1–2000 chars; empty or oversized text returns 422). A
sample rendered report is included at `docs/sample_report_CMP-02.pdf`.

### Docker

```bash
docker build -t incident-agent .
docker run -p 8000:8000 --env-file .env incident-agent
```

Same API at http://localhost:8000. The key is injected at runtime via
`--env-file` and is never baked into the image.

## Test scenarios

Each case below is tested via both the offline tests and the live evaluation.

| Scenario | Case | Input | Expected behaviour |
|---|---|---|---|
| Normal investigation | TC001 | `Etcher-03 triggered RF Power Instability at 10:35. Tool down for 45 minutes. Lot LOT1055 running. Similar alarm occurred twice last week.` | Full report; surfaces all three prior occurrences (H101–H103) with dates; escalates R001–R005 |
| Ambiguous alarm | TC002 | `CMP-02 pressure alarm. Downtime 18 minutes. Lot LOT1056.` | Infers CMP205 from the wording; notifies Manufacturing Supervisor only (R005) |
| Repeated incident | TC001 / TC002 | (as above) | TC001 recurs within 7 days and escalates (R002, R004); TC002 has one prior in 30 days — flagged as a repeat, nothing over-escalated |
| High severity + downtime | TC003 | `CVD-05 has gas flow deviation, MFC actual flow below setpoint. Downtime 35 min.` | Escalates on downtime (R001) and High severity (R003) |
| Over-escalation guard | TC004 | `Litho-01 alignment failure for lot LOT1058, downtime 12 min.` | R005 only; recommends camera clean / calibration per SOP008 |
| Unknown equipment | TC005 | `Unknown tool ALPHA-99 has alarm ZX999.` | Stops after the failed lookup; asks the user to verify — no fabrication |
| Unknown alarm, known equipment | CUST-A | `Etcher-03 reports alarm XYZ888, downtime 20 minutes.` | Proceeds on the incident record's real code and states the correction explicitly, naming both codes |
| Missing information | CUST-B | `Etcher-03 is down.` | Full correct report — every fact recovered from the incident record |
| Known equipment, no open incident | CUST-C | `Diffusion-02 pump making abnormal noise, please investigate.` | Asks for the incident details — nothing invented |

## Testing

```bash
pytest -q                    # 12 offline tests, no API key, ~5 s
python eval.py               # live evaluation, all 8 cases -> eval_results.md
python eval.py --runs 3      # stability check
python -m src.llm_client     # offline self-check of the tool loop
```

The committed matrices show 5 runs per case on both model tiers, scored
deterministically on status, exact escalation rule set, required citations,
shipped issues, and fabricated ids: **40/40** on the default tier
(`eval_results.md`) and **36/40** on the deliberately weak stress tier
(`eval_results_flashlite.md`) — the escalation verdict was correct in all 80
runs and zero errors shipped silently on either tier. See the design
document, section 6.

## How correctness is guaranteed

- **Escalation verdict by construction.** Code calls the rule engine with the
  five inputs read from the locked incident record — there is no transcription
  step for the model to get wrong.
- **Cross-check against ground truth.** Every id cited in the report is
  verified against the dataset; every triggered rule and its contact must
  appear correctly; the incident's own facts must be present.
- **Reflection.** If the cross-check finds issues, the model is asked once to
  fix exactly those issues; the fix is adopted only if it is non-empty and
  strictly reduces the issue count.
- **Honest failure paths.** Unknown equipment produces a clarification
  request, follow-up answers pass the same fabricated-id scan as reports, and
  any unresolved issues ship visibly in `cross_check_issues`.

## Notes and limitations

- `gemini-3-flash-preview` is a preview model; at peak hours Google may
  occasionally return 503s. The client retries with backoff automatically
  (visible as `[retry]` lines), so runs complete — they may just take longer.
  The GA `gemini-3.1-flash-lite` is a one-line fallback in `config.py`.
- Follow-up sessions are held in process memory; a production deployment
  would externalize the store and add expiry.
- Some maintenance records in the dataset post-date the incidents; they are
  returned as-is and documented as an assumption in the design document.
- Prose wording inside the analysis sections is the model's responsibility;
  identifiers, escalation facts, and the verdict are guarded by code. See the
  design document's limitations section for examples.