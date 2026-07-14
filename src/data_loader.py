"""
Data layer: loads the Excel workbook into memory for tools to query.

Date columns are parsed with errors="raise": fail loudly rather than let a
bad value or column name silently stay a string. The dict returned by
get_sheets() is read-only for callers — .copy() before mutating.
"""

from functools import lru_cache

import pandas as pd

from src.config import DATA_PATH

# Named explicitly so we never touch "downtime_minutes".
DATE_COLUMNS = {
    "current_incidents": ["timestamp"],
    "incident_history": ["timestamp"],
    "sensor_readings": ["timestamp"],
    "lot_wip": ["start_time"],
    "maintenance_records": ["maintenance_date"],
    "sop_knowledge_base": ["last_updated"],
}


def load_all_sheets() -> dict[str, pd.DataFrame]:
    """Read every sheet and parse the declared date columns."""
    sheets = pd.read_excel(DATA_PATH, sheet_name=None)
    for sheet_name, date_cols in DATE_COLUMNS.items():
        for col in date_cols:
            sheets[sheet_name][col] = pd.to_datetime(
                sheets[sheet_name][col], errors="raise"
            )
    return sheets


@lru_cache(maxsize=1)
def get_sheets() -> dict[str, pd.DataFrame]:
    """Return all sheets, loading on first call and caching thereafter."""
    return load_all_sheets()


# Self-check: python -m src.data_loader
if __name__ == "__main__":
    sheets = get_sheets()
    print(f"Loaded {len(sheets)} sheets")
    for sheet_name, date_cols in DATE_COLUMNS.items():
        for col in date_cols:
            dtype = sheets[sheet_name][col].dtype
            ok = "OK " if "datetime" in str(dtype) else "BAD"
            print(f"  [{ok}] {sheet_name}.{col}: {dtype}")