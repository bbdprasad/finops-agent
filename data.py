"""Load cost exports into one canonical frame.

Standalone on purpose so this reporter runs without the rest of the stack.
Maps common Azure Cost Management and FOCUS column names, lifts tags out of the
Tags blob, and keeps untagged spend rather than dropping it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from config import (
    AZURE_STORAGE_ACCOUNT_URL,
    AZURE_STORAGE_CONNECTION_STRING,
    AZURE_STORAGE_CONTAINER,
    AZURE_STORAGE_PREFIX,
    BUSINESS_SCOPE,
    BUSINESS_SCOPE_COLUMN,
    DATABRICKS_HTTP_PATH,
    DATABRICKS_SERVER_HOSTNAME,
    DATABRICKS_TOKEN,
    DATABRICKS_VIEW,
    DATA_SOURCE,
    SOURCE_LOOKBACK_YEARS,
)

COLUMN_MAP = {
    "usage_date": ["date", "usagedate", "usagedatetime", "chargeperiodstart",
                   "billingperiodstartdate", "chargedate"],
    "subscription_name": ["subscriptionname", "subaccountname", "subscription"],
    "resource_group": ["resourcegroup", "resourcegroupname"],
    "resource_id": ["resourceid", "instanceid", "resourcename", "instancename"],
    "service_name": ["metercategory", "servicename", "consumedservice",
                     "servicecategory"],
    "meter": ["metername", "meter", "metersubcategory"],
    "region": ["resourcelocation", "region", "location"],
    "cost": ["costinbillingcurrency", "billedcost", "effectivecost", "pretaxcost",
             "cost"],
    "currency": ["billingcurrency", "billingcurrencycode", "currency"],
    "application": ["application", "app", "applicationname"],
    "environment": ["environment", "env"],
    "cost_center": ["costcenter", "cost_centre"],
    "owner": ["owner", "technicalowner"],
    "business_unit": ["businessunit", "business_unit", "invoice_section_name",
                       "invoicesectionname", "businessarea"],
}

TAG_TARGETS = {
    "application": ["application", "app", "appname", "service"],
    "environment": ["environment", "env", "stage"],
    "cost_center": ["costcenter", "costcentre"],
    "owner": ["owner", "technicalowner"],
}

COLUMNS = list(COLUMN_MAP.keys())


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _parse_tags(raw) -> dict[str, str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    text = str(raw).strip()
    if not text or text in {"nan", "{}"}:
        return {}
    if not text.startswith("{"):
        text = "{" + text + "}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = dict(re.findall(r'"?([\w\-. ]+)"?\s*[:=]\s*"([^"]*)"', text))
    if not isinstance(parsed, dict):
        return {}
    return {_norm(k): str(v).strip() for k, v in parsed.items() if v}


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    lookup = {_norm(c): c for c in df.columns}
    out = pd.DataFrame(index=df.index)

    for canonical, candidates in COLUMN_MAP.items():
        src = next((lookup[c] for c in candidates if c in lookup), None)
        out[canonical] = df[src] if src else None

    tag_col = next((lookup[c] for c in ("tags", "resourcetags") if c in lookup), None)
    if tag_col is not None:
        tags = df[tag_col].map(_parse_tags)
        for target, keys in TAG_TARGETS.items():
            lifted = tags.map(lambda t, k=keys: next((t[x] for x in k if x in t), None))
            out[target] = out[target].where(out[target].notna(), lifted)

    out["usage_date"] = pd.to_datetime(out["usage_date"], errors="coerce",
                                       format="mixed").dt.normalize()
    out["cost"] = pd.to_numeric(out["cost"], errors="coerce")

    for col in ("application", "environment", "service_name", "resource_group",
                "resource_id", "subscription_name", "meter", "cost_center", "owner"):
        out[col] = out[col].map(lambda v: str(v).strip() if pd.notna(v) else None)

    # Tag coverage is the finding. Label the gap, never drop it.
    out["is_untagged"] = out["application"].isna()
    out["application"] = out["application"].fillna("untagged")
    out["environment"] = out["environment"].fillna("unknown")

    return out.dropna(subset=["usage_date", "cost"])[COLUMNS + ["is_untagged"]]


def apply_scope(df: pd.DataFrame) -> pd.DataFrame:
    """Limit analysis to the configured business scope when that column exists."""
    if not BUSINESS_SCOPE:
        return df
    column = BUSINESS_SCOPE_COLUMN
    if column not in df.columns:
        return df
    values = df[column].fillna("").astype(str)
    if not values.str.strip().any():
        return df
    return df[values.str.contains(BUSINESS_SCOPE, case=False, regex=False)]


def load(data_dir: Path) -> pd.DataFrame:
    files = sorted(p for p in data_dir.glob("**/*")
                   if p.suffix.lower() in {".csv", ".tsv"})
    if not files:
        raise FileNotFoundError(f"No CSV or TSV files in {data_dir}")
    frames = []
    for path in files:
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open("rb") as raw:
            prefix = raw.read(4)
        encoding = "utf-16" if prefix.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        frames.append(normalise(pd.read_csv(
            path, sep=sep, encoding=encoding, low_memory=False)))
    return pd.concat(frames, ignore_index=True)


def load_blob() -> pd.DataFrame:
    """Load CSV/TSV exports from Azure Blob Storage or ADLS Gen2."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise RuntimeError("Install azure-storage-blob and azure-identity for blob mode") from exc

    if AZURE_STORAGE_CONNECTION_STRING:
        client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    elif AZURE_STORAGE_ACCOUNT_URL:
        client = BlobServiceClient(AZURE_STORAGE_ACCOUNT_URL, credential=DefaultAzureCredential())
    else:
        raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL")
    container = client.get_container_client(AZURE_STORAGE_CONTAINER)
    frames = []
    for blob in container.list_blobs(name_starts_with=AZURE_STORAGE_PREFIX):
        if Path(blob.name).suffix.lower() not in {".csv", ".tsv"}:
            continue
        raw = container.download_blob(blob.name).readall()
        encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        sep = "\t" if blob.name.lower().endswith(".tsv") else ","
        frames.append(normalise(pd.read_csv(
            pd.io.common.BytesIO(raw), sep=sep, encoding=encoding, low_memory=False)))
    if not frames:
        raise FileNotFoundError("No CSV or TSV exports found in the configured blob path")
    return apply_scope(pd.concat(frames, ignore_index=True))


def load_databricks() -> pd.DataFrame:
    """Query a curated Databricks view for the configured historical scope."""
    try:
        from databricks import sql
    except ImportError as exc:
        raise RuntimeError("Install databricks-sql-connector for Databricks mode") from exc
    if not all((DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH,
                DATABRICKS_TOKEN, DATABRICKS_VIEW)):
        raise RuntimeError("Set Databricks hostname, HTTP path, token, and view")
    query = (
        f"SELECT * FROM {DATABRICKS_VIEW} "
        f"WHERE usage_date >= add_months(current_date(), {-12 * SOURCE_LOOKBACK_YEARS})"
    )
    with sql.connect(
        server_hostname=DATABRICKS_SERVER_HOSTNAME,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            columns = [column[0] for column in cursor.description]
            frame = pd.DataFrame(cursor.fetchall(), columns=columns)
    return apply_scope(normalise(frame))


def load_source(data_dir: Path | None = None) -> pd.DataFrame:
    """Load the configured source and apply the business scope."""
    if DATA_SOURCE == "blob":
        return load_blob()
    if DATA_SOURCE == "databricks":
        return load_databricks()
    return apply_scope(load(data_dir or Path.cwd()))
