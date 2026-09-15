"""Configuration. Everything from the environment, nothing hardcoded."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DATA_DIR = Path(os.getenv("FINOPS_DATA_DIR", BASE / "data"))
OUT_DIR = Path(os.getenv("FINOPS_OUT_DIR", BASE / "reports"))

# --- Data source ------------------------------------------------------------
DATA_SOURCE = os.getenv("FINOPS_DATA_SOURCE", "local").lower()
SOURCE_LOOKBACK_YEARS = int(os.getenv("FINOPS_SOURCE_LOOKBACK_YEARS", "2"))
BUSINESS_SCOPE = os.getenv("FINOPS_BUSINESS_SCOPE", "Supply Chain")
BUSINESS_SCOPE_COLUMN = os.getenv("FINOPS_BUSINESS_SCOPE_COLUMN", "business_unit")

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_STORAGE_ACCOUNT_URL = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
AZURE_STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "")
AZURE_STORAGE_PREFIX = os.getenv("AZURE_STORAGE_PREFIX", "")

DATABRICKS_SERVER_HOSTNAME = os.getenv("DATABRICKS_SERVER_HOSTNAME", "")
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
DATABRICKS_VIEW = os.getenv("DATABRICKS_VIEW", "")

CURRENCY = os.getenv("FINOPS_CURRENCY", "DKK")

# --- Detection thresholds ---------------------------------------------------
# These are the knobs that decide whether the report is useful or ignored.
# Start conservative. Every false positive costs you credibility with the reader,
# and a report nobody trusts is worse than no report.

# Robust z-score above which a day counts as anomalous.
Z_THRESHOLD = float(os.getenv("FINOPS_Z_THRESHOLD", "3.5"))

# Absolute floor. A 400% jump on 12 DKK is noise, not a finding. This single
# threshold removes more false positives than anything else here.
MIN_ANOMALY_COST = float(os.getenv("FINOPS_MIN_ANOMALY_COST", "500"))

# Trailing window used to build the baseline, and the recent window compared
# against it for sustained step changes.
BASELINE_DAYS = int(os.getenv("FINOPS_BASELINE_DAYS", "28"))
RECENT_DAYS = int(os.getenv("FINOPS_RECENT_DAYS", "7"))

# A step change must move by at least this fraction to be reported.
STEP_CHANGE_PCT = float(os.getenv("FINOPS_STEP_CHANGE_PCT", "0.25"))

# Separate, higher floor for findings expressed as monthly impact. 500 DKK/month
# on a 400k estate is not a finding, it is rounding, and reporting it is how a
# report loses its reader. Spikes are judged on a single day so they keep the
# lower floor above.
MIN_MONTHLY_IMPACT = float(os.getenv("FINOPS_MIN_MONTHLY_IMPACT", "5000"))

# Billing restates for several days. Ignore the tail so a partial day does not
# read as spend collapsing to zero.
TRAILING_LAG_DAYS = int(os.getenv("FINOPS_TRAILING_LAG_DAYS", "2"))

# Refuse to report if the newest data is older than this. Set to 0 to disable
# the freshness gate entirely for local or exploratory reports.
MAX_STALENESS_DAYS = int(os.getenv("FINOPS_MAX_STALENESS_DAYS", "0"))

# Tag coverage below this is flagged as a governance finding.
TAG_COVERAGE_TARGET = float(os.getenv("FINOPS_TAG_COVERAGE_TARGET", "0.95"))

# --- Model ------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NARRATE_MODEL = os.getenv("FINOPS_NARRATE_MODEL", "claude-sonnet-5")

# --- Email ------------------------------------------------------------------
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("FINOPS_MAIL_FROM", "")
MAIL_TO = [a.strip() for a in os.getenv("FINOPS_MAIL_TO", "").split(",") if a.strip()]
MAIL_SUBJECT_PREFIX = os.getenv("FINOPS_MAIL_SUBJECT_PREFIX", "[FinOps]")
