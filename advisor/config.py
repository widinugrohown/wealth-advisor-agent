"""Central configuration.

Anomaly thresholds live here on purpose: tuning one threshold at a time is the
only way a precision/recall change can be attributed to anything (see README).
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- anomaly rules (R1-R8, see README "Detection rules") ---
ROBUST_Z_THRESHOLD = float(os.getenv("ROBUST_Z_THRESHOLD", "3.5"))     # R1
R1_MATERIALITY_MULT = float(os.getenv("R1_MATERIALITY_MULT", "3.0"))    # R1: unusual AND material
IQR_MULTIPLIER = float(os.getenv("IQR_MULTIPLIER", "3.0"))              # R2
TREND_RATIO = float(os.getenv("TREND_RATIO", "2.5"))                    # R3
STRUCTURING_THRESHOLD_IDR = int(os.getenv("STRUCTURING_THRESHOLD_IDR", "10000000"))  # R4
STRUCTURING_WINDOW_DAYS = 7
STRUCTURING_MIN_COUNT = 3
NEW_COUNTERPARTY_P95_MULT = float(os.getenv("NEW_COUNTERPARTY_P95_MULT", "3.0"))     # R5
DORMANT_GAP_DAYS = 90                                                   # R6
DORMANT_ACTIVE_COUNT = 3
DORMANT_ACTIVE_WINDOW_DAYS = 7
CONCENTRATION_PCT = float(os.getenv("CONCENTRATION_PCT", "0.80"))       # R8

# --- minimum data per rule (below this the rule stays silent, not "no finding = all good") ---
MIN_TX_FOR_ZSCORE = 20
MIN_TX_PER_CATEGORY = 8
MIN_MONTHS_FOR_TREND = 6

SEVERITY_HIGH_ABS_Z = 5.0

# --- mock CRM transport ---
CRM_BASE_URL = os.getenv("CRM_BASE_URL", "http://127.0.0.1:8077")
CRM_TIMEOUT_S = float(os.getenv("CRM_TIMEOUT_S", "2.0"))
CRM_RETRIES = int(os.getenv("CRM_RETRIES", "2"))
CRM_BACKOFF_S = (0.5, 1.0)

# --- paths ---
DATA_DIR = os.getenv("DATA_DIR", "data")
RUNS_DIR = os.getenv("RUNS_DIR", "runs")
MEMORY_PATH = os.getenv("MEMORY_PATH", "memory/insights.jsonl")
