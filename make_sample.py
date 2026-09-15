"""Synthetic cost export with known planted findings, so detection can be verified.

Planted deliberately:
  1. step change   ippl-datalake Databricks steps up ~45% from 21 days ago
  2. spike         omni-ops Cosmos DB one-day spike 5 days ago
  3. new_spend     rg-hero-aps-prod appears 5 days ago
  4. stopped       rg-accellos-prod stops 6 days ago
  5. weekend       nonprod does not shut down (no weekend dip)
  6. nonprod>=prod omni-ops nonprod rivals prod
  7. untagged      ~4% of spend has no application tag
"""
import csv, datetime as dt, random
from pathlib import Path

random.seed(7)
TODAY = dt.date.today()
START = TODAY - dt.timedelta(days=120)
END = TODAY - dt.timedelta(days=1)

APPS = {
    "ippl-datalake": ["Azure Databricks", "Storage", "Azure Data Factory v2", "Event Hubs"],
    "omni-ops":      ["Azure Functions", "Azure Cosmos DB", "Storage", "API Management"],
    "aptos":         ["Virtual Machines", "SQL Database", "Storage"],
    "logility":      ["Virtual Machines", "Storage"],
}
ENVS = [("prod", 1.0), ("nonprod", 0.55), ("dev", 0.25)]
SUBS = {"prod": "sc-prod-001", "nonprod": "sc-nonprod-001", "dev": "sc-dev-001"}
rows = []

def add(day, app, env, svc, cost, rg=None):
    rows.append({
        "Date": day.isoformat(),
        "SubscriptionName": SUBS[env],
        "ResourceGroup": rg or f"rg-{app}-{env}",
        "ResourceId": f"/subs/{SUBS[env]}/{rg or f'rg-{app}-{env}'}/{svc.lower().replace(' ','-')}",
        "MeterCategory": svc,
        "MeterName": f"{svc} Standard",
        "ResourceLocation": "westeurope",
        "CostInBillingCurrency": round(max(cost, 0), 2),
        "BillingCurrency": "DKK",
        "Tags": f'"application":"{app}","environment":"{env}","costcenter":"D6835"',
    })

day = START
while day <= END:
    ago = (END - day).days
    weekend = day.weekday() >= 5
    for app, services in APPS.items():
        for env, ew in ENVS:
            for svc in services:
                base = random.uniform(200, 700) * ew
                # prod dips slightly at weekends; nonprod does NOT (finding 5)
                if weekend and env == "prod":
                    base *= 0.75
                # finding 6: omni-ops nonprod sized like prod
                if app == "omni-ops" and env == "nonprod":
                    base *= 1.75
                # finding 1: sustained step change 21 days ago
                if app == "ippl-datalake" and svc == "Azure Databricks" and ago <= 21:
                    base *= 1.45
                # finding 2: one-day spike 5 days ago
                if app == "omni-ops" and svc == "Azure Cosmos DB" and ago == 5 and env == "prod":
                    base *= 9.0
                add(day, app, env, svc, base)
    # finding 3: new resource group appears 5 days ago
    if ago <= 5:
        add(day, "hero-aps", "prod", "Virtual Machines", random.uniform(1400, 1800), "rg-hero-aps-prod")
    # finding 4: decommissioned 14 days ago, clear of the restatement lag window
    if ago > 14:
        add(day, "accellos", "prod", "Virtual Machines", random.uniform(900, 1200), "rg-accellos-prod")
    # finding 7: untagged spend
    rows.append({
        "Date": day.isoformat(), "SubscriptionName": "sc-prod-001",
        "ResourceGroup": "rg-shared-unassigned", "ResourceId": "/subs/sc-prod-001/rg-shared-unassigned/misc",
        "MeterCategory": "Storage", "MeterName": "Storage Standard",
        "ResourceLocation": "westeurope",
        "CostInBillingCurrency": round(random.uniform(1100, 1500), 2),
        "BillingCurrency": "DKK", "Tags": "",
    })
    day += dt.timedelta(days=1)

Path("data").mkdir(parents=True, exist_ok=True)
with open("data/sample_costs.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
print(f"{len(rows)} rows, {START} to {END}")
