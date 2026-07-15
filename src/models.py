"""
Typed structures for tool outputs and, later, the report.

All datetimes are serialized as ISO strings — these models are JSON-ified
for the LLM.
"""

from typing import Literal

from pydantic import BaseModel


class AlarmDetail(BaseModel):
    """One alarm code, from alarm_reference."""
    alarm_code: str
    severity: Literal["High", "Medium", "Low"]
    description: str
    probable_causes: list[str]
    applicable_tool_type: str

class CurrentIncident(BaseModel):
    """The open incident on a piece of equipment right now, from current_incidents."""
    incident_id: str
    timestamp: str
    alarm_code: str
    alarm_description: str
    downtime_minutes: int
    affected_lot: str | None = None
    status: str

class EquipmentDetail(BaseModel):
    equipment_id: str
    equipment_name: str
    tool_type: str
    line: str
    bay: str
    vendor: str
    model: str
    install_year: int
    status: str
    process_area: str
    primary_engineer_id: str
    current_incident: CurrentIncident | None = None

class SopDetail(BaseModel):
    """One standard operating procedure, from sop_knowledge_base."""
    sop_id: str
    alarm_code: str
    tool_type: str
    title: str
    troubleshooting_steps: list[str]
    revision: str
    last_updated: str


class SimilarIncident(BaseModel):
    """One past incident with the same equipment and alarm, from incident_history."""
    incident_id: str
    timestamp: str
    equipment_id: str
    alarm_code: str
    affected_lot: str
    downtime_minutes: int
    root_cause: str
    corrective_action: str
    closure_status: str
    product_impact: str


class MaintenanceRecord(BaseModel):
    """One maintenance record, from maintenance_records."""
    maintenance_id: str
    maintenance_date: str
    equipment_id: str
    maintenance_type: str
    component: str
    engineer: str
    remarks: str
    status: str


class SensorReading(BaseModel):
    """One sensor time-point tied to an incident, from sensor_readings."""
    incident_id: str
    timestamp: str
    equipment_id: str
    rf_power: float
    chamber_temp: float
    gas_flow: float
    pressure: float
    vibration: float


class EngineerContact(BaseModel):
    """One person, from engineer_directory."""
    engineer_id: str
    name: str
    role: str
    specialty: str
    email: str
    shift: str


class RuleCheck(BaseModel):
    """One rule's verdict plus the evidence it was judged on; untriggered
    checks are kept for report grounding."""
    rule_id: str
    condition: str
    observed: str
    triggered: bool
    level: Literal["escalate", "notify"]
    target_role: str
    contacts: list[EngineerContact] = []


class EscalationResult(BaseModel):
    """Deterministic escalation verdict for one incident."""
    equipment_id: str
    alarm_code: str
    incident_timestamp: str
    count_7d: int
    count_30d: int
    requires_escalation: bool
    rule_checks: list[RuleCheck]


class InvestigationResult(BaseModel):
    """The thin agent's output: the model's report, the verdict, cross-check
    findings, and the tool trace. Or a clarification request."""
    status: Literal["report", "needs_clarification"]
    report: str | None = None
    escalation: EscalationResult | None = None
    cross_check_issues: list[str] = []
    tool_trace: list[dict] = []
    clarification: str | None = None
    reflection_used: bool = False
    session_id: str | None = None