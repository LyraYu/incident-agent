"""
REST shell over investigate(). No business logic here — the API and the CLI
call the same investigate(); batch-test results apply to both.
"""

from fastapi import FastAPI
from pydantic import BaseModel

from src.agent import investigate
from src.models import InvestigationResult

app = FastAPI(title="Incident Investigation Agent")


class IncidentRequest(BaseModel):
    text: str


@app.post("/investigate", response_model=InvestigationResult)
def investigate_incident(req: IncidentRequest) -> InvestigationResult:
    """Investigate a free-text incident description.

    Returns a structured report with an escalation verdict, cross-check
    findings, and the full tool trace — or a clarification request if the
    equipment cannot be identified.
    """
    return investigate(req.text)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}