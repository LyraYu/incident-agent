"""
Tools the agent calls to read the dataset.

All tools read from get_sheets() (cached, read-only). Single-record misses
return None, translated to an explicit not-found payload at the agent-loop
boundary; multi-record misses return an empty list. Columns are returned in
full — deciding what to surface is the report layer's job.
"""

from datetime import datetime

import pandas as pd

from src.data_loader import get_sheets
from src.models import (
    AlarmDetail,
    EngineerContact,
    EquipmentDetail,
    EscalationResult,
    MaintenanceRecord,
    RuleCheck,
    SensorReading,
    SimilarIncident,
    SopDetail,
    CurrentIncident,
)


def get_alarm_details(
    alarm_code: str | None = None, tool_type: str | None = None
) -> AlarmDetail | list[AlarmDetail] | None:
    """Look up an alarm by code/description (precise), or list all alarms for a
    tool type (explore) to resolve a vague description against real candidates.
    """

    if alarm_code is None and tool_type is None:
        raise ValueError("pass alarm_code or tool_type")
    
    df = get_sheets()["alarm_reference"]

    # Explore mode: all alarms for a tool type -> list.
    if tool_type is not None and alarm_code is None:
        matches = df[df["applicable_tool_type"].str.upper() == tool_type.strip().upper()]
        return [
            AlarmDetail(
                alarm_code=r["alarm_code"],
                severity=r["severity"],
                description=r["description"],
                probable_causes=[c.strip() for c in r["probable_causes"].split(",")],
                applicable_tool_type=r["applicable_tool_type"],
            )
            for _, r in matches.iterrows()
        ]

    # Precise mode: one alarm by code, then by description -> single or None.
    if alarm_code is not None:
        code = alarm_code.strip().upper()
        hit = df[df["alarm_code"].str.upper() == code]
        if hit.empty:
            hit = df[df["description"].str.upper() == code]
        if hit.empty:
            return None
        row = hit.iloc[0]
        return AlarmDetail(
            alarm_code=row["alarm_code"],
            severity=row["severity"],
            description=row["description"],
            probable_causes=[c.strip() for c in row["probable_causes"].split(",")],
            applicable_tool_type=row["applicable_tool_type"],
        )

    return None


def _current_incident(equipment_id):
    """The open incident on this equipment right now, or None."""
    df = get_sheets()["current_incidents"]
    hit = df[df["equipment_id"].str.upper() == equipment_id.upper()]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return CurrentIncident(
        incident_id=r["incident_id"],
        timestamp=str(r["timestamp"]),
        alarm_code=r["alarm_code"],
        alarm_description=r["alarm_description"],
        downtime_minutes=int(r["downtime_minutes"]),
        affected_lot=None if pd.isna(r["affected_lot"]) else str(r["affected_lot"]),
        status=r["status"],
    )


def get_equipment_details(identifier: str) -> EquipmentDetail | None:
    """Look up one equipment by id or name, joined with its current incident."""
    df = get_sheets()["equipment_master"]
    key = identifier.strip().upper()
    hit = df[df["equipment_id"].str.upper() == key]
    if hit.empty:
        hit = df[df["equipment_name"].str.upper() == key]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return EquipmentDetail(
        equipment_id=row["equipment_id"],
        equipment_name=row["equipment_name"],
        tool_type=row["tool_type"],
        line=row["line"],
        bay=row["bay"],
        vendor=row["vendor"],
        model=row["model"],
        install_year=int(row["install_year"]),
        status=row["status"],
        process_area=row["process_area"],
        primary_engineer_id=row["primary_engineer_id"],
        current_incident=_current_incident(row["equipment_id"]),
    )


def get_sop(alarm_code: str) -> SopDetail | None:
    """Look up the SOP for an alarm code in sop_knowledge_base."""
    df = get_sheets()["sop_knowledge_base"]
    code = alarm_code.strip().upper()
    hit = df[df["alarm_code"].str.upper() == code]
    if hit.empty:
        return None
    row = hit.iloc[0]
    steps = [s.strip() for s in row["troubleshooting_steps"].split(";")]
    return SopDetail(
        sop_id=row["sop_id"],
        alarm_code=row["alarm_code"],
        tool_type=row["tool_type"],
        title=row["title"],
        troubleshooting_steps=steps,
        revision=row["revision"],
        last_updated=str(row["last_updated"].date()),
    )


def get_similar_incidents(
    equipment_id: str, alarm_code: str, limit: int = 5
) -> list[SimilarIncident]:
    """Past incidents on the same equipment with the same alarm, newest-first.

    Capped narrative feed for the LLM;
    check_escalation counts uncapped.
    """
    df = get_sheets()["incident_history"]
    eq = equipment_id.strip().upper()
    code = alarm_code.strip().upper()
    matches = (
        df[(df["equipment_id"].str.upper() == eq) & (df["alarm_code"].str.upper() == code)]
        .sort_values("timestamp", ascending=False)
        .head(limit)
    )
    return [
        SimilarIncident(
            incident_id=r["incident_id"],
            timestamp=str(r["timestamp"]),
            equipment_id=r["equipment_id"],
            alarm_code=r["alarm_code"],
            affected_lot=r["affected_lot"],
            downtime_minutes=int(r["downtime_minutes"]),
            root_cause=r["root_cause"],
            corrective_action=r["corrective_action"],
            closure_status=r["closure_status"],
            product_impact=r["product_impact"],
        )
        for _, r in matches.iterrows()
    ]


def get_maintenance_history(
    equipment_id: str, limit: int = 5
) -> list[MaintenanceRecord]:
    """Recent maintenance records for one piece of equipment, newest-first."""
    df = get_sheets()["maintenance_records"]
    eq = equipment_id.strip().upper()
    matches = (
        df[df["equipment_id"].str.upper() == eq]
        .sort_values("maintenance_date", ascending=False)
        .head(limit)
    )
    return [
        MaintenanceRecord(
            maintenance_id=r["maintenance_id"],
            maintenance_date=str(r["maintenance_date"].date()),
            equipment_id=r["equipment_id"],
            maintenance_type=r["maintenance_type"],
            component=r["component"],
            engineer=r["engineer"],
            remarks=r["remarks"],
            status=r["status"],
        )
        for _, r in matches.iterrows()
    ]


def get_sensor_readings(incident_id: str) -> list[SensorReading]:
    """All sensor points for one incident, chronological"""
    df = get_sheets()["sensor_readings"]
    inc = incident_id.strip().upper()
    matches = df[df["incident_id"].str.upper() == inc].sort_values(
        "timestamp", ascending=True
    )
    return [
        SensorReading(
            incident_id=r["incident_id"],
            timestamp=str(r["timestamp"]),
            equipment_id=r["equipment_id"],
            rf_power=float(r["rf_power"]),
            chamber_temp=float(r["chamber_temp"]),
            gas_flow=float(r["gas_flow"]),
            pressure=float(r["pressure"]),
            vibration=float(r["vibration"]),
        )
        for _, r in matches.iterrows()
    ]


# ---------------------------------------------------------------------------
# check_escalation: deterministic rule engine (impure shell + pure core)
# ---------------------------------------------------------------------------

def _count_recurrences(
    equipment_id: str, alarm_code: str, incident_ts: pd.Timestamp
) -> tuple[int, int]:
    """Count prior same-equipment, same-alarm incidents in trailing
    calendar-day windows anchored at incident_ts. Strictly-before rows only;
    the current incident is added in check_escalation."""
    df = get_sheets()["incident_history"]
    eq = equipment_id.strip().upper()
    code = alarm_code.strip().upper()
    same = df[
        (df["equipment_id"].str.upper() == eq)
        & (df["alarm_code"].str.upper() == code)
        & (df["timestamp"] < incident_ts)
    ]
    day = incident_ts.normalize()
    days = same["timestamp"].dt.normalize()
    count_7d = int((days >= day - pd.Timedelta(days=7)).sum())
    count_30d = int((days >= day - pd.Timedelta(days=30)).sum())
    return count_7d, count_30d


def _resolve_role(target_role: str) -> list[EngineerContact]:
    """Resolve an escalation target to people: exact role match, then exact
    name. The directory mixes the two ("Vendor Support" is a name), and exact
    matching keeps "Process Engineer" from catching "Senior Process Engineer".
    """
    df = get_sheets()["engineer_directory"]
    key = target_role.strip().lower()
    hits = df[df["role"].str.strip().str.lower() == key]
    if hits.empty:
        hits = df[df["name"].str.strip().str.lower() == key]
    return [
        EngineerContact(
            engineer_id=r["engineer_id"],
            name=r["name"],
            role=r["role"],
            specialty=r["specialty"],
            email=r["email"],
            shift=r["shift"],
        )
        for _, r in hits.iterrows()
    ]


def _evaluate_rules(
    downtime_minutes: int,
    severity: str | None,
    count_7d: int,
    count_30d: int,
    affected_lot: str | None,
) -> list[RuleCheck]:
    """Apply the five escalation rules (source: escalation_rules sheet) to the given facts."""
    return [
        RuleCheck(
            rule_id="R001",
            condition="downtime_minutes > 30",
            observed=f"downtime = {downtime_minutes} minutes",
            triggered=downtime_minutes > 30,
            level="escalate",
            target_role="Senior Equipment Engineer",
        ),
        RuleCheck(
            rule_id="R002",
            condition="same alarm on same equipment >= 2 times within 7 days",
            observed=f"{count_7d} occurrence(s) in the 7-day window, including the current incident",
            triggered=count_7d >= 2,
            level="escalate",
            target_role="Engineering Manager",
        ),
        RuleCheck(
            rule_id="R003",
            condition="alarm severity == High",
            observed=f"severity = {severity}",
            triggered=severity == "High",
            level="escalate",
            target_role="Process Engineer",
        ),
        RuleCheck(
            rule_id="R004",
            condition="same alarm on same equipment > 3 times within 30 days",
            observed=f"{count_30d} occurrence(s) in the 30-day window (current + {count_30d - 1} prior)",
            triggered=count_30d > 3,
            level="escalate",
            target_role="Vendor Support",
        ),
        RuleCheck(
            rule_id="R005",
            condition="affected_lot is not null",
            observed=(
                f"affected lot = {affected_lot}"
                if affected_lot
                else "no affected lot recorded"
            ),
            triggered=affected_lot is not None,
            level="notify",
            target_role="Manufacturing Supervisor",
        ),
    ]


def check_escalation(
    equipment_id: str,
    alarm_code: str,
    incident_timestamp: str | datetime,
    downtime_minutes: int,
    affected_lot: str | None = None,
) -> EscalationResult:
    """Deterministic escalation verdict for one incident.

    Recurrence counts are recomputed here from incident_history (not taken from
    a tool result) and include the current incident: in production it would sit
    in the same event log the query scans.
    """
    ts = pd.to_datetime(incident_timestamp)
    lot = (
        affected_lot.strip()
        if isinstance(affected_lot, str) and affected_lot.strip()
        else None
    )

    # Resolve names/descriptions to ids so history matching cannot silently miss.
    equipment = get_equipment_details(equipment_id)
    eq_id = equipment.equipment_id if equipment else equipment_id.strip().upper()
    alarm = get_alarm_details(alarm_code)
    code = alarm.alarm_code if alarm else alarm_code.strip().upper()
    severity = alarm.severity if alarm else None

    prior_7d, prior_30d = _count_recurrences(eq_id, code, ts)
    # +1: the current incident itself counts.
    count_7d, count_30d = prior_7d + 1, prior_30d + 1

    checks = _evaluate_rules(downtime_minutes, severity, count_7d, count_30d, lot)
    for check in checks:
        if check.triggered:
            check.contacts = _resolve_role(check.target_role)

    return EscalationResult(
        equipment_id=eq_id,
        alarm_code=code,
        incident_timestamp=str(ts),
        count_7d=count_7d,
        count_30d=count_30d,
        requires_escalation=any(
            c.triggered and c.level == "escalate" for c in checks
        ),
        rule_checks=checks,
    )
