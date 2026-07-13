"""
Central configuration: single place for settings that may change.
Keeping these here means changing the model or data path touches one file.
"""

from pathlib import Path

# --- Project root: this file now lives in src/, so climb one extra level ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Dataset location ---
DATA_PATH = PROJECT_ROOT / "data" / "Incident_Investigation_dataset.xlsx"

# --- LLM model ---
GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_THINKING_LEVEL = "low"   # 3.x 延迟档位: minimal/low/medium/high