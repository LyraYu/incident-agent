"""
REST shell over investigate(). No business logic here — the API and the CLI
call the same investigate(); batch-test results apply to both.
"""

import uuid

import markdown
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from src.agent import follow_up, investigate
from src.models import InvestigationResult
from src.ui import SESSIONS, FollowUpRequest, IncidentRequest, router as ui_router

app = FastAPI(title="Incident Investigation Agent")
app.include_router(ui_router)

# In-memory, single-process session store; a production deployment would
# externalize this (e.g. Redis) and add expiry.
SESSIONS: dict[str, list] = {}


class IncidentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class FollowUpRequest(BaseModel):
    session_id: str
    question: str = Field(min_length=1, max_length=2000)


class FollowUpResponse(BaseModel):
    answer: str
    issues: list[str] = []


@app.post("/investigate", response_model=InvestigationResult)
def investigate_incident(req: IncidentRequest) -> InvestigationResult:
    """Investigate a free-text incident description.

    Returns a structured report with an escalation verdict, cross-check
    findings, the full tool trace, and a session_id for follow-up questions —
    or a clarification request if the equipment or alarm cannot be identified.
    """
    history: list = []
    result = investigate(req.text, history=history)
    result.session_id = str(uuid.uuid4())
    SESSIONS[result.session_id] = history
    return result


@app.post("/follow_up", response_model=FollowUpResponse)
def follow_up_incident(req: FollowUpRequest) -> FollowUpResponse:
    """Ask a follow-up question about a previous investigation session."""
    history = SESSIONS.get(req.session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="unknown or expired session_id")
    answer, issues = follow_up(req.question, history)
    return FollowUpResponse(answer=answer, issues=issues)


@app.post("/investigate/report", response_class=PlainTextResponse)
def investigate_report_only(req: IncidentRequest) -> str:
    """Plain-text report only — the human-readable view of /investigate."""
    r = investigate(req.text)
    return r.report if r.status == "report" else r.clarification


@app.get("/report/demo", response_class=HTMLResponse)
def report_demo(text: str = Query(min_length=1, max_length=2000)) -> str:
    """Rendered HTML view of the report — open directly in a browser."""
    r = investigate(text)
    body = r.report if r.status == "report" else r.clarification
    return ("<article style='max-width:52rem;margin:2rem auto;"
            "font-family:sans-serif;line-height:1.6'>"
            f"{markdown.markdown(body, extensions=['nl2br'])}</article>")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}