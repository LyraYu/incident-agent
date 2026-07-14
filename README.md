# Incident Investigation Agentic AI Assistant

An agentic AI assistant that helps semiconductor equipment engineers investigate
machine downtime incidents. It accepts a free-text incident description,
retrieves evidence from the plant dataset through tool calls, reasons over the
context with a real LLM (Gemini), and produces a structured investigation
report with a deterministic escalation verdict.

The LLM runs the whole investigation in a single tool loop — identifying the
equipment, resolving the alarm, gathering evidence, and writing the report.
Everything that must be correct is owned by code: a deterministic rule engine
computes the escalation verdict from five inputs read directly off the incident
record, a cross-check validates every id and fact in the report against the
dataset, and one reflection pass fixes only the issues found. Architecture,
trade-offs, and stress-test data are in the design document.

## Project structure

```
src/
  agent.py        # investigation prompt, orchestration, cross-check, reflection
  tools.py        # 7 dataset tools, incl. the deterministic check_escalation rule engine
  llm_client.py   # Gemini client, tool declarations, manual tool loop, retry logic
  models.py       # typed (pydantic) structures: tool outputs, verdict, report
  data_loader.py  # cached read-only access to the Excel dataset
  config.py       # model + thinking-level configuration
  api.py          # FastAPI shell over investigate()
tests/
  test_agent.py   # offline tests mirroring the five official test cases
data/
  Incident_Investigation_dataset.xlsx
requirements.txt
```

## Setup

**Prerequisites:** Python 3.11+, a Google Gemini API key
(free at https://aistudio.google.com).

```bash
python -m venv venv
venv\Scripts\activate          # Windows   (macOS/Linux: source venv/bin/activate)
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
stress-tested on `gemini-3.1-flash-lite` (a weaker, cheaper tier) with zero
silent errors — correctness does not depend on the model tier. See the design
document for the data.

## Running

### CLI

```bash
python -m src.agent live "Etcher-03 triggered RF Power Instability at 10:35. Tool down for 45 minutes. Lot LOT1055 running. Similar alarm occurred twice last week."
```

On Windows, run `set PYTHONUTF8=1` first for clean unicode output.

The output shows the tool-call trace (code-side calls tagged `[code]`), the
report, and the cross-check result.

### REST API

```bash
uvicorn src.api:app --port 8000
```

- Interactive docs (try requests in the browser): http://localhost:8000/docs
- Health check: `GET /health`

```bash
curl -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"CMP-02 pressure alarm. Downtime 18 minutes. Lot LOT1056.\"}"
```

The response is the full `InvestigationResult`: report, escalation verdict,
cross-check findings, and the complete tool trace.

## Sample prompts (the five official test cases)

| # | Input | Expected behaviour |
|---|-------|--------------------|
| TC001 | `Etcher-03 triggered RF Power Instability at 10:35. Tool down for 45 minutes. Lot LOT1055 running. Similar alarm occurred twice last week.` | Retrieves EQ001 / RF101 / H101–H103 / SOP001; full escalation (R001–R005); corrects "twice" to the recorded three prior occurrences |
| TC002 | `CMP-02 pressure alarm. Downtime 18 minutes. Lot LOT1056.` | Infers CMP205 from the vague wording; notifies the Manufacturing Supervisor only (R005) — no over-escalation |
| TC003 | `CVD-05 has gas flow deviation, MFC actual flow below setpoint. Downtime 35 min.` | Retrieves GAS012 context; escalates on downtime (R001) and High severity (R003) |
| TC004 | `Litho-01 alignment failure for lot LOT1058, downtime 12 min.` | Avoids over-escalation (R005 only); recommends camera clean / calibration per SOP008 |
| TC005 | `Unknown tool ALPHA-99 has alarm ZX999.` | Stops after the failed lookup and asks the user to verify the equipment name — no fabrication |

## Testing

```bash
pytest -q                    # offline tests mirroring TC001–TC005 (no API key needed)
python -m src.llm_client     # offline self-check of the tool loop (no API key needed)
```

Live end-to-end runs use the CLI command above (API key required).

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
- **Honest failure paths.** Unknown equipment or alarm produces a clarification
  request, and any unresolved issues ship visibly in `cross_check_issues`.

## Notes and limitations

- `gemini-3-flash-preview` is a preview model; under load Google may return
  503s. The client retries with backoff automatically; switching to the GA
  `gemini-3.1-flash-lite` (one line in `config.py`) avoids this.
- Some maintenance records in the dataset post-date the incidents; they are
  returned as-is and this is documented as an assumption in the design
  document.
- Prose wording inside the analysis sections is the model's responsibility;
  identifiers, escalation facts, and the verdict are guarded by code. See the
  design document's limitations section for examples.