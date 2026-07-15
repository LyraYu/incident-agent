"""
Web UI for engineers: describe an incident, read the report, ask follow-ups.
A single self-contained page served by the existing API process — no build
step, no frontend framework, no new dependencies. All logic stays in
investigate()/follow_up(); this file only renders.
"""

import html
import uuid

import markdown
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.agent import follow_up, investigate

# In-memory, single-process session store; a production deployment would
# externalize this (e.g. Redis) and add expiry. Shared by the REST API.
SESSIONS: dict[str, list] = {}


class IncidentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class FollowUpRequest(BaseModel):
    session_id: str
    question: str = Field(min_length=1, max_length=2000)


router = APIRouter()

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Incident Investigation Agent</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 52rem;
         margin: 2rem auto; line-height: 1.6; padding: 0 1rem; }}
  form {{ display: flex; gap: .5rem; margin-bottom: 1rem; }}
  input[type=text] {{ flex: 1; padding: .6rem; font-size: 1rem;
         border: 1px solid #bbb; border-radius: 6px; }}
  button {{ padding: .6rem 1.2rem; font-size: 1rem; cursor: pointer; }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.5rem;
         margin-top: 1rem; }}
  .issues {{ color: #b00; }}
  .hint {{ color: #777; font-size: .9rem; }}
</style></head><body>
<h2>Incident Investigation Agent</h2>
<form method="get" action="/ui">
  <input type="text" name="text" placeholder="Describe the incident, e.g. 'CMP-02 pressure alarm. Downtime 18 minutes. Lot LOT1056.'" value="{text}" minlength="1" maxlength="2000" required>
  <button type="submit">Investigate</button>
</form>
<p class="hint">Investigation takes one to two minutes — the agent is querying the plant dataset.</p>
{body}
</body></html>"""

_FOLLOWUP_FORM = """
<form method="get" action="/ui/follow_up">
  <input type="hidden" name="session_id" value="{sid}">
  <input type="text" name="question" placeholder="Ask a follow-up, e.g. 'What fixed H102 last time?'" minlength="1" maxlength="2000" required>
  <button type="submit">Ask</button>
</form>"""


def _card(md_text: str, issues: list[str] | None = None) -> str:
    rendered = markdown.markdown(md_text, extensions=["nl2br"])
    tail = ""
    if issues:
        items = "".join(f"<li>{i}</li>" for i in issues)
        tail = f"<p class='issues'>Cross-check issues:</p><ul class='issues'>{items}</ul>"
    return f"<div class='card'>{rendered}{tail}</div>"


@router.get("/ui", response_class=HTMLResponse)
def ui_home(text: str = "") -> str:
    """The engineer-facing page: investigate and follow up in one place."""
    if not text.strip():
        return _PAGE.format(text="", body="")
    req = IncidentRequest(text=text.strip())          # same validation as the API
    history: list = []
    result = investigate(req.text, history=history)
    if result.status == "needs_clarification":
        return _PAGE.format(text=html.escape(req.text),
                            body=_card(result.clarification))
    sid = str(uuid.uuid4())
    SESSIONS[sid] = history
    body = (_card(result.report, result.cross_check_issues)
            + _FOLLOWUP_FORM.format(sid=sid))
    return _PAGE.format(text=html.escape(req.text), body=body)


@router.get("/ui/follow_up", response_class=HTMLResponse)
def ui_follow_up(session_id: str, question: str) -> str:
    history = SESSIONS.get(session_id)
    if history is None:
        return _PAGE.format(text="", body=_card(
            "**Session expired.** Start a new investigation above."))
    req = FollowUpRequest(session_id=session_id, question=question.strip())
    answer, issues = follow_up(req.question, history)
    body = (_card(answer, issues) + _FOLLOWUP_FORM.format(sid=session_id))
    return _PAGE.format(text="", body=body)