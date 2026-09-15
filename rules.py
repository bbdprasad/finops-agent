"""Recommendation rules, with deterministic scoring.

Be honest about what cost data alone can tell you. Without utilisation metrics
(CPU, GPU, IOPS) or an Advisor feed, you cannot detect an idle VM. What you can
detect from billing data is structural waste: patterns in how spend is shaped
that indicate a problem regardless of utilisation.

That turns out to be where the larger money usually is anyway. Rightsizing one
VM saves tens. Non production running at production scale, or a weekend
shutdown that never got implemented, saves thousands.

Every rule states its confidence. A rule that infers rather than observes says
so, and the score reflects it. The model narrating this report cannot change a
score, only explain it.
"""

from __future__ import annotations

import pandas as pd

import config

# effort: how much work to implement.  risk: production blast radius.
EFFORT = {"trivial": 1.0, "moderate": 0.5, "project": 0.2}
RISK = {"none": 1.0, "low": 0.7, "production": 0.3}


def _score(saving: float, confidence: float, effort: str, risk: str) -> float:
    return round(saving * confidence * EFFORT[effort] * RISK[risk], 2)


def _monthly(df: pd.DataFrame, mask=None) -> float:
    """Scale a window's spend to a monthly figure."""
    scope = df if mask is None else df[mask]
    if scope.empty:
        return 0.0
    days = scope["usage_date"].nunique()
    return float(scope["cost"].sum()) / max(days, 1) * 30


def generate(df: pd.DataFrame) -> list[dict]:
    recs: list[dict] = []
    latest = df["usage_date"].max()
    window = df[df["usage_date"] >= latest - pd.Timedelta(days=29)]
    if window.empty:
        return recs

    total_monthly = _monthly(window)

    # --- 1. Non production weekend spend --------------------------------------
    # Non production running at weekends means no shutdown schedule. With daily
    # grain this is the closest proxy available for out of hours waste, and it is
    # usually the single largest finding.
    nonprod = window[~window["environment"].str.lower().isin(["prod", "production"])]
    if not nonprod.empty:
        weekend = nonprod[nonprod["usage_date"].dt.dayofweek >= 5]
        weekday = nonprod[nonprod["usage_date"].dt.dayofweek < 5]
        if not weekend.empty and not weekday.empty:
            weekend_daily = weekend["cost"].sum() / max(weekend["usage_date"].nunique(), 1)
            weekday_daily = weekday["cost"].sum() / max(weekday["usage_date"].nunique(), 1)
            # If weekend spend is close to weekday spend, nothing is shutting down.
            if weekday_daily > 0 and weekend_daily / weekday_daily > 0.7:
                saving = weekend_daily * 8  # ~8 weekend days a month
                if saving >= config.MIN_ANOMALY_COST:
                    recs.append({
                        "rule": "nonprod_no_weekend_shutdown",
                        "title": "Non production is not shutting down at weekends",
                        "detail": (
                            f"Non production averages {weekend_daily:,.0f} "
                            f"{config.CURRENCY}/day at weekends against "
                            f"{weekday_daily:,.0f} on weekdays, so nothing is "
                            "being stopped."
                        ),
                        "monthly_saving": round(saving, 2),
                        "confidence": 0.8,
                        "effort": "moderate",
                        "risk": "none",
                        "action": "Add a start/stop schedule to non production compute.",
                        "score": _score(saving, 0.8, "moderate", "none"),
                    })

    # --- 2. Non production ratio ---------------------------------------------
    # A rule that fires on every application conveys nothing. The first version
    # of this flagged all four apps in the test estate, which is technically
    # correct and operationally useless: if everything is an outlier, nothing is.
    #
    # So report the estate ratio once as a single finding, and flag individual
    # applications only when they sit well above the estate baseline. That turns
    # a wall of rows into one number plus the genuine exceptions.
    env_is_prod = window["environment"].str.lower().isin(["prod", "production"])
    prod_total = float(window.loc[env_is_prod, "cost"].sum())
    non_total = float(window.loc[~env_is_prod, "cost"].sum())

    if prod_total > 0:
        estate_ratio = non_total / prod_total
        if estate_ratio > 0.5:
            saving = (non_total - prod_total * 0.4) / max(
                window["usage_date"].nunique(), 1) * 30
            if saving >= config.MIN_ANOMALY_COST:
                recs.append({
                    "rule": "estate_nonprod_ratio",
                    "title": (
                        f"Non production is {estate_ratio:.0%} of production "
                        "across the estate"
                    ),
                    "detail": (
                        f"{non_total:,.0f} {config.CURRENCY} non production "
                        f"against {prod_total:,.0f} production over the window. "
                        "A healthy ratio is usually well under half."
                    ),
                    "monthly_saving": round(saving, 2),
                    "confidence": 0.7,
                    "effort": "project",
                    "risk": "low",
                    "action": (
                        "Set a non production budget as a percentage of "
                        "production and review environment count per application."
                    ),
                    "score": _score(saving, 0.7, "project", "low"),
                })

        # Per application, only the genuine outliers against the estate baseline.
        by_app = window.groupby(["application", env_is_prod])["cost"].sum().unstack(
            fill_value=0.0)
        if True in by_app.columns and False in by_app.columns:
            for app, row in by_app.iterrows():
                if app == "untagged":
                    continue
                prod, non = float(row[True]), float(row[False])
                if prod <= 0:
                    continue
                ratio = non / prod
                # Must exceed the estate baseline by half again, not just be high.
                if ratio < max(estate_ratio * 1.5, 1.0):
                    continue
                saving = (non - prod * 0.4) / max(
                    window["usage_date"].nunique(), 1) * 30
                if saving >= config.MIN_ANOMALY_COST:
                    recs.append({
                        "rule": "nonprod_outlier",
                        "title": (
                            f"{app} non production is {ratio:.0%} of its "
                            "production, against an estate norm of "
                            f"{estate_ratio:.0%}"
                        ),
                        "detail": (
                            f"{app} spends {non:,.0f} {config.CURRENCY} in non "
                            f"production against {prod:,.0f} in production, well "
                            "above the rest of the estate."
                        ),
                        "monthly_saving": round(saving, 2),
                        "confidence": 0.6,
                        "effort": "project",
                        "risk": "low",
                        "action": (
                            "Review non production sizing with the application "
                            "owner."
                        ),
                        "score": _score(saving, 0.6, "project", "low"),
                    })

    # --- 3. Tag coverage ------------------------------------------------------
    # Not a saving. A governance finding, and the reason every other number in
    # the report carries a caveat.
    untagged_monthly = _monthly(window, window["is_untagged"])
    if total_monthly > 0:
        coverage = 1 - (untagged_monthly / total_monthly)
        if coverage < config.TAG_COVERAGE_TARGET:
            recs.append({
                "rule": "tag_coverage_below_target",
                "title": f"Tag coverage is {coverage:.0%}",
                "detail": (
                    f"{untagged_monthly:,.0f} {config.CURRENCY}/month is not "
                    "attributable to an application, so it cannot be forecast, "
                    "showed back or owned."
                ),
                "monthly_saving": 0.0,
                "confidence": 1.0,
                "effort": "moderate",
                "risk": "none",
                "action": (
                    "Identify the untagged resource groups and assign owners. "
                    "Consider an Azure Policy requiring the application tag."
                ),
                "score": round(untagged_monthly * 0.1, 2),  # governance weight
            })

    # --- 4. Growth outliers ---------------------------------------------------
    # A service growing much faster than the estate is either a real change or an
    # unnoticed leak. Worth a question either way.
    recent = window[window["usage_date"] >= latest - pd.Timedelta(days=6)]
    prior = window[(window["usage_date"] < latest - pd.Timedelta(days=6))
                   & (window["usage_date"] >= latest - pd.Timedelta(days=27))]
    if not recent.empty and not prior.empty:
        r = recent.groupby("service_name")["cost"].sum() / recent["usage_date"].nunique()
        p = prior.groupby("service_name")["cost"].sum() / prior["usage_date"].nunique()
        for service in r.index.intersection(p.index):
            if p[service] <= 0:
                continue
            growth = (r[service] - p[service]) / p[service]
            monthly_delta = (r[service] - p[service]) * 30
            if growth > 0.25 and monthly_delta >= config.MIN_ANOMALY_COST:
                recs.append({
                    "rule": "service_growth_outlier",
                    "title": f"{service} grew {growth:.0%} week on week",
                    "detail": (
                        f"Daily spend moved from {p[service]:,.0f} to "
                        f"{r[service]:,.0f} {config.CURRENCY}, roughly "
                        f"{monthly_delta:,.0f} a month if it holds."
                    ),
                    "monthly_saving": round(float(monthly_delta), 2),
                    "confidence": 0.5,
                    "effort": "moderate",
                    "risk": "low",
                    "action": (
                        "Confirm with the owning team whether this growth is "
                        "intended."
                    ),
                    "score": _score(float(monthly_delta), 0.5, "moderate", "low"),
                })

    # --- 5. Single meter concentration ---------------------------------------
    # One meter dominating an application usually means one misconfigured thing,
    # which makes it a cheap fix relative to its size.
    for app, group in window.groupby("application"):
        if app == "untagged" or group["cost"].sum() < config.MIN_ANOMALY_COST * 4:
            continue
        by_meter = group.groupby("meter")["cost"].sum().sort_values(ascending=False)
        if len(by_meter) < 3:
            continue
        share = by_meter.iloc[0] / group["cost"].sum()
        if share > 0.6:
            monthly = _monthly(group) * share
            recs.append({
                "rule": "meter_concentration",
                "title": f"One meter is {share:.0%} of {app}",
                "detail": (
                    f"{by_meter.index[0]} accounts for {share:.0%} of {app} spend, "
                    f"about {monthly:,.0f} {config.CURRENCY}/month."
                ),
                "monthly_saving": round(monthly * 0.15, 2),
                "confidence": 0.4,
                "effort": "moderate",
                "risk": "low",
                "action": (
                    "Inspect the dominant meter's configuration. Concentration "
                    "this high often traces to one setting."
                ),
                "score": _score(monthly * 0.15, 0.4, "moderate", "low"),
            })

    recs.sort(key=lambda r: r["score"], reverse=True)
    return recs
