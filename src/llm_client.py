"""
LLM layer: the Gemini client, tool declarations, and the manual tool loop.

Carries whatever system prompt it is handed; the investigation prompt lives in
src/agent.py. Tool misses (None / empty list) become {"found": false} payloads.
"""

import os
import re
import sys
import time
from typing import Any, Callable

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from src import tools
from src.config import GEMINI_MODEL, GEMINI_THINKING_LEVEL

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_equipment_details": tools.get_equipment_details,
    "get_alarm_details": tools.get_alarm_details,
    "get_similar_incidents": tools.get_similar_incidents,
    "get_maintenance_history": tools.get_maintenance_history,
    "get_sensor_readings": tools.get_sensor_readings,
    "get_sop": tools.get_sop,
    "check_escalation": tools.check_escalation,
}

_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOL_DECLARATIONS = [
    {
        "name": "get_equipment_details",
        "description": (
            "Look up one piece of equipment by id ('EQ001') or by name "
            "('Etcher-03'). Returns master data (tool type, location, vendor, "
            "model, install year, primary engineer) AND the equipment's current "
            "incident if one is open — including the incident's real timestamp "
            "and incident_id. Use those for check_escalation and "
            "get_sensor_readings; do not guess a time or an incident id."
        ),
        "parameters": {
            "type": "object",
            "properties": {"identifier": _STR},
            "required": ["identifier"],
        },
    },
    {
        "name": "get_alarm_details",
        "description": (
            "Look up an alarm. Pass alarm_code (a code like 'RF101' or an exact "
            "description like 'RF Power Instability') to get one alarm's severity "
            "and probable causes. OR pass tool_type (like 'CMP') to list every "
            "alarm for that equipment type — use this to resolve a vague alarm "
            "description by matching it against the real candidates. If both are "
            "given, alarm_code wins."
        ),
        "parameters": {
            "type": "object",
            "properties": {"alarm_code": _STR, "tool_type": _STR},
        },
    },
    {
        "name": "get_similar_incidents",
        "description": (
            "Past incidents on the same equipment with the same alarm, newest "
            "first (max 5). Use to see whether the problem is recurring and "
            "what fixed it before."
        ),
        "parameters": {
            "type": "object",
            "properties": {"equipment_id": _STR, "alarm_code": _STR},
            "required": ["equipment_id", "alarm_code"],
        },
    },
    {
        "name": "get_maintenance_history",
        "description": (
            "Recent maintenance records for one equipment id, newest first "
            "(max 5). Always check: recent maintenance is a prime suspect."
        ),
        "parameters": {
            "type": "object",
            "properties": {"equipment_id": _STR},
            "required": ["equipment_id"],
        },
    },
    {
        "name": "get_sensor_readings",
        "description": (
            "Sensor time series around one current incident, by incident id "
            "('INC001'). Shows which physical channel deviated, if any."
        ),
        "parameters": {
            "type": "object",
            "properties": {"incident_id": _STR},
            "required": ["incident_id"],
        },
    },
    {
        "name": "get_sop",
        "description": "Official troubleshooting steps for an alarm code.",
        "parameters": {
            "type": "object",
            "properties": {"alarm_code": _STR},
            "required": ["alarm_code"],
        },
    },
    {
        "name": "check_escalation",
        "description": (
            "Deterministic escalation verdict for the incident. Pass the "
            "facts: equipment, alarm, timestamp 'YYYY-MM-DD HH:MM', downtime "
            "minutes, affected lot if any. Returns which rules trigger and "
            "who to escalate to or notify."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "equipment_id": _STR,
                "alarm_code": _STR,
                "incident_timestamp": _STR,
                "downtime_minutes": _INT,
                "affected_lot": _STR,
            },
            "required": [
                "equipment_id",
                "alarm_code",
                "incident_timestamp",
                "downtime_minutes",
            ],
        },
    },
]


def get_client() -> genai.Client:
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set — put it in .env")
    return genai.Client(api_key=key)


def _serialize(result: Any, tool_name: str, args: dict) -> dict:
    if result is None:
        return {"found": False, "message": f"{tool_name}: no match for {args}"}
    if isinstance(result, BaseModel):
        return {"found": True, "result": result.model_dump()}
    if isinstance(result, list):
        return {
            "found": bool(result),
            "count": len(result),
            "results": [item.model_dump() for item in result],
        }
    return {"found": True, "result": result}


def execute_tool(name: str, args: dict) -> dict:
    """Run one tool call from the model and return a JSON-safe payload."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        result = fn(**args)
    except Exception as exc:
        # Boundary: a bad model-supplied call comes back as an error payload
        # the model can react to, never as a crashed loop.
        return {"error": f"{type(exc).__name__}: {exc}"}
    return _serialize(result, name, args)


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    """Delay before retrying: honor the server's hint, else back off."""
    hint = re.search(r"retry in ([0-9.]+)s", str(exc))
    if hint:
        return max(float(hint.group(1)) + 1.0, 5.0)
    return min(15.0 * (attempt + 1), 60.0)


def _call_with_retry(call: Callable[[], Any]) -> Any:
    """429 = per-minute quota, 5xx = transient overload; wait it out."""
    for attempt in range(6):
        try:
            return call()
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code not in (429, 500, 503, 504) or attempt == 5:
                raise
            delay = _retry_delay_seconds(exc, attempt)
            print(f"  [retry] {code} — waiting {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)


def append_exchange(history: list, user: str, model: str) -> None:
    """Record one user/model turn pair in a session history."""
    history.append(types.Content(role="user", parts=[types.Part(text=user)]))
    history.append(types.Content(role="model", parts=[types.Part(text=model)]))


def run_tool_loop(
    system: str,
    user: str,
    generate_fn: Callable | None = None,
    max_rounds: int = 8,
    history: list | None = None,
) -> tuple[str, list[dict]]:
    """Manual tool loop. Returns (final_text, trace).

    `system` is whatever prompt the caller hands in — this module has no
    opinion on its content. trace is one dict per executed call:
    {"tool", "args", "ok", "payload"}. Returns ("", trace) if the round
    budget is exhausted.
    """
    if generate_fn is None:
        client = get_client()
        # thinking_level is a Gemini 3.x knob; older tiers reject it, so it is
        # sent only when configured. Switching models touches config.py alone.
        thinking = (
            types.ThinkingConfig(thinking_level=GEMINI_THINKING_LEVEL)
            if GEMINI_THINKING_LEVEL else None
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            thinking_config=thinking,
        )

        def generate_fn(contents):
            return _call_with_retry(
                lambda: client.models.generate_content(
                    model=GEMINI_MODEL, contents=contents, config=config
                )
            )

    contents: list = history if history is not None else []
    contents.append(types.Content(role="user", parts=[types.Part(text=user)]))
    trace: list[dict] = []

    for _ in range(max_rounds):
        response = generate_fn(contents)
        content = response.candidates[0].content
        calls = [
            part.function_call
            for part in (content.parts or [])
            if getattr(part, "function_call", None)
        ]
        if not calls:
            contents.append(content)
            return (response.text or ""), trace

        contents.append(content)
        parts = []
        for call in calls:
            args = dict(call.args or {})
            payload = execute_tool(call.name, args)
            trace.append(
                {"tool": call.name, "args": args, "ok": "error" not in payload, "payload": payload}
            )
            parts.append(
                types.Part.from_function_response(name=call.name, response=payload)
            )
        contents.append(types.Content(role="tool", parts=parts))

    return "", trace


# Self-check: python -m src.llm_client                  (offline, fake model)
#             python -m src.llm_client list             (models this key serves)
#             python -m src.llm_client live ["question"]  (transport smoke test)
#
# NOTE: the live prompt here is a generic transport check ("use the tools when
# they help") — NOT the investigation prompt. The real pipeline is
# `python -m src.agent live "..."`. This module stays business-agnostic.
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        client = get_client()
        for m in client.models.list():
            actions = (
                getattr(m, "supported_actions", None)
                or getattr(m, "supported_generation_methods", None)
                or []
            )
            if not actions or "generateContent" in actions:
                print(m.name)
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "live":
        # Generic transport smoke: proves key + model + function-calling wire
        # format work. Deliberately not the investigation prompt.
        system = "Answer using the tools when they help."
        user = sys.argv[2] if len(sys.argv) > 2 else (
            "Look up alarm RF101 and tell me its severity and probable causes."
        )
        text, trace = run_tool_loop(system, user)
        print("=== tool calls ===")
        for step in trace:
            print(f"  {step['tool']}({step['args']}) ok={step['ok']}")
        print("\n=== answer ===")
        print(text)
        sys.exit(0)

    # Offline: scripted fake model exercises dispatch, serialization, and loop.
    from types import SimpleNamespace as NS

    def resp_calls(*name_args):
        parts = [NS(function_call=NS(name=n, args=a), text=None) for n, a in name_args]
        return NS(candidates=[NS(content=NS(role="model", parts=parts))], text=None)

    def resp_text(t):
        return NS(
            candidates=[NS(content=NS(role="model", parts=[NS(function_call=None, text=t)]))],
            text=t,
        )

    script = iter([
        resp_calls(
            ("get_alarm_details", {"alarm_code": "RF101"}),
            ("get_alarm_details", {"alarm_code": "ZX999"}),
        ),
        resp_calls(
            ("check_escalation", {
                "equipment_id": "Etcher-03",
                "alarm_code": "RF Power Instability",
                "incident_timestamp": "2026-06-22 10:35",
                "downtime_minutes": 45,
                "affected_lot": "LOT1055",
            }),
            ("get_sop", {"wrong_param": "RF101"}),
        ),
        resp_text("done"),
    ])

    text, trace = run_tool_loop("sys", "user", generate_fn=lambda _: next(script))

    assert text == "done"
    assert [t["ok"] for t in trace] == [True, True, True, False]
    assert all("payload" in t for t in trace)
    hit = execute_tool("get_alarm_details", {"alarm_code": "RF101"})
    miss = execute_tool("get_alarm_details", {"alarm_code": "ZX999"})
    assert hit["found"] is True and hit["result"]["severity"] == "High"
    assert miss["found"] is False and "ZX999" in miss["message"]
    esc = execute_tool("check_escalation", {
        "equipment_id": "Etcher-03",
        "alarm_code": "RF Power Instability",
        "incident_timestamp": "2026-06-22 10:35",
        "downtime_minutes": 45,
        "affected_lot": "LOT1055",
    })
    assert esc["result"]["count_30d"] == 4 and esc["result"]["requires_escalation"]
    bad = execute_tool("get_sop", {"wrong_param": "RF101"})
    assert "error" in bad
    real_429 = Exception("Quota exceeded ... Please retry in 5.755360501s.")
    assert 6.5 < _retry_delay_seconds(real_429, 0) < 7.0
    assert _retry_delay_seconds(Exception("no hint"), 1) == 30.0
    print("offline loop self-check: PASS")
    print("trace:", [(t["tool"], t["ok"]) for t in trace])