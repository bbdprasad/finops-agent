"""Anomaly detection. Statistics, not a language model.

Four detectors, because "cost anomaly" is four different things and they need
different responses:

  spike        one day well outside the baseline. Often a one off job or a
               purchase. Check it, rarely act on it.
  step_change  a sustained shift in the run rate. This is the one that matters,
               because it compounds every month until someone notices.
  new_spend    something that was not there before. Frequently the real cause of
               a spike, and more actionable than the spike itself.
  stopped      spend that fell to zero. Could be a successful decommission or a
               broken pipeline, and you want to know which.

Three design choices worth understanding:

1. Median and MAD, not mean and standard deviation. Cost data is spiky. One large
   day inflates the standard deviation enough to hide the next one, so a
   mean-based z-score gets progressively blinder the more anomalies you have.
   Median absolute deviation does not move when a single point does.

2. Day of week comparison. Cloud cost has strong weekly seasonality: non
   production idles at weekends, batch jobs land on schedules. Compare a Monday
   to other Mondays or every Monday looks anomalous.

3. An absolute floor. A 400 percent jump on 12 DKK is arithmetic, not a finding.
   This threshold removes more false positives than the other two combined, and
   false positives are how a report gets filtered to a folder nobody opens.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config

# Scale factor making MAD comparable to a standard deviation for normal data.
MAD_TO_SIGMA = 1.4826


def _robust_z(series: pd.Series) -> pd.Series:
    median = series.median()
    mad = (series - median).abs().median()
    if mad == 0:
        # Degenerate spread. Fall back to standard deviation rather than
        # dividing by zero and calling everything infinitely anomalous.
        std = series.std()
        if not std or np.isnan(std):
            return pd.Series(0.0, index=series.index)
        return (series - series.mean()) / std
    return (series - median) / (mad * MAD_TO_SIGMA)


def _usable(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Trim the restatement tail. Recent days are incomplete and will otherwise
    read as spend collapsing."""
    latest = df["usage_date"].max()
    cutoff = latest - pd.Timedelta(days=config.TRAILING_LAG_DAYS)
    return df[df["usage_date"] <= cutoff], cutoff


def detect(df: pd.DataFrame, dimensions: tuple[str, ...] = (
        "application", "service_name", "app_service")) -> list[dict]:
    """Run the detectors at several grains and merge.

    Grain matters more than the statistics. A 45% jump in one service inside a
    four service application is roughly an 11% move at application level, which
    sits under any sensible threshold. Detect only at application level and you
    systematically miss the service level shifts that are the actual cause.

    So: detect per application, per service, and per application-service pair,
    then keep the highest impact finding per scope so the same event is not
    reported three times.
    """
    trimmed, as_of = _usable(df)
    if trimmed.empty:
        return []

    if "app_service" in dimensions:
        trimmed = trimmed.assign(
            app_service=trimmed["application"] + " / " + trimmed["service_name"]
        )

    findings: list[dict] = []
    for dim in dimensions:
        findings.extend(_detect_one(trimmed, dim))
    findings.extend(_new_and_stopped(trimmed))

    findings = _dedupe(findings)
    findings.sort(key=lambda f: f["impact"], reverse=True)
    for f in findings:
        f["as_of"] = as_of.date().isoformat()
    return findings


def _dedupe(findings: list[dict]) -> list[dict]:
    """One event, one finding.

    Greedy rather than dictionary keyed: keep findings in order of specificity
    then impact, and drop any whose scope is contained in one already kept for
    the same type and date. A dict keyed on (type, date) cannot do this, because
    a third finding needs comparing against every finding already kept, not just
    the one currently occupying the slot. That bug reported the same Databricks
    step change twice.
    """
    def specificity(f: dict) -> tuple:
        return (" / " in str(f["value"]), f["impact"])

    kept: list[dict] = []
    for f in sorted(findings, key=specificity, reverse=True):
        a = str(f["value"])
        duplicate = any(
            k["type"] == f["type"] and k.get("date") == f.get("date")
            and (a in str(k["value"]) or str(k["value"]) in a)
            for k in kept
        )
        if not duplicate:
            kept.append(f)
    return kept


def _detect_one(df: pd.DataFrame, dimension: str) -> list[dict]:
    trimmed = df

    daily = (trimmed.groupby([dimension, "usage_date"], dropna=False)["cost"]
             .sum().reset_index())
    daily["dow"] = daily["usage_date"].dt.dayofweek

    findings: list[dict] = []

    for key, group in daily.groupby(dimension):
        group = group.sort_values("usage_date")
        if len(group) < 14:
            continue  # not enough history to have a baseline worth trusting

        # --- spike: same weekday comparison across the recent window --------
        # Scan the whole recent window rather than only the newest day. A spike
        # three days ago is still news if nobody has looked since. Anything older
        # than the window is out of scope by design: this is a report on what
        # changed, not a historical audit.
        window_start = group["usage_date"].max() - pd.Timedelta(
            days=config.RECENT_DAYS - 1)
        for dow, dow_group in group.groupby("dow"):
            if len(dow_group) < 4:
                continue
            z = _robust_z(dow_group["cost"])
            baseline = dow_group["cost"].median()
            recent = dow_group[dow_group["usage_date"] >= window_start]
            for idx in recent.index:
                cost = dow_group.loc[idx, "cost"]
                if (z.loc[idx] > config.Z_THRESHOLD
                        and cost - baseline >= config.MIN_ANOMALY_COST):
                    findings.append({
                        "type": "spike",
                        "dimension": dimension,
                        "value": key,
                        "date": dow_group.loc[idx, "usage_date"].date().isoformat(),
                        "cost": round(float(cost), 2),
                        "baseline": round(float(baseline), 2),
                        "delta": round(float(cost - baseline), 2),
                        "z_score": round(float(z.loc[idx]), 1),
                        "impact": round(float(cost - baseline), 2),
                    })

        # --- month over month: catches shifts the 7 day window has absorbed --
        # A step that happened three weeks ago is partly inside the 28 day
        # baseline, so the short window measures it as diluted and misses it.
        # Comparing the last 30 days against the prior 30 catches sustained
        # shifts regardless of when they started, which is what a monthly report
        # is actually for.
        if len(group) >= 50:
            last30 = group.tail(30)["cost"].median()
            prev30 = group.iloc[-60:-30]["cost"].median()
            if prev30 > 0:
                mom = (last30 - prev30) / prev30
                impact = (last30 - prev30) * 30
                if (abs(mom) >= config.STEP_CHANGE_PCT
                        and abs(impact) >= config.MIN_MONTHLY_IMPACT):
                    findings.append({
                        "type": "step_change",
                        "dimension": dimension,
                        "value": key,
                        "date": group["usage_date"].max().date().isoformat(),
                        "daily_now": round(float(last30), 2),
                        "daily_before": round(float(prev30), 2),
                        "change_pct": round(float(mom * 100), 1),
                        "monthly_impact": round(float(impact), 2),
                        "horizon": "30d vs prior 30d",
                        "impact": round(abs(float(impact)), 2),
                    })

        # --- step change: sustained shift in run rate ------------------------
        recent = group.tail(config.RECENT_DAYS)
        prior = group.iloc[-(config.RECENT_DAYS + config.BASELINE_DAYS):
                           -config.RECENT_DAYS]
        if len(prior) >= 14 and len(recent) >= 3:
            # Median on both sides. The mean version reported the Cosmos DB
            # one day spike as a step change, because one 9x day across seven
            # days moves a mean by more than the 20% threshold. A step change is
            # a shift in the typical day, which is what the median measures.
            recent_mean = recent["cost"].median()
            prior_mean = prior["cost"].median()
            if prior_mean > 0:
                change = (recent_mean - prior_mean) / prior_mean
                monthly_impact = (recent_mean - prior_mean) * 30
                if (abs(change) >= config.STEP_CHANGE_PCT
                        and abs(monthly_impact) >= config.MIN_MONTHLY_IMPACT):
                    findings.append({
                        "type": "step_change",
                        "dimension": dimension,
                        "value": key,
                        "date": recent["usage_date"].max().date().isoformat(),
                        "daily_now": round(float(recent_mean), 2),
                        "daily_before": round(float(prior_mean), 2),
                        "change_pct": round(float(change * 100), 1),
                        "monthly_impact": round(float(monthly_impact), 2),
                        "horizon": f"{config.RECENT_DAYS}d vs prior "
                                   f"{config.BASELINE_DAYS}d",
                        "impact": round(abs(float(monthly_impact)), 2),
                    })

    return findings


def _new_and_stopped(df: pd.DataFrame) -> list[dict]:
    """Resource groups that appeared or disappeared between the two windows.

    Resource group rather than resource id: individual resources churn constantly
    in any healthy environment and reporting each one is noise. A whole resource
    group appearing is a deployment.
    """
    latest = df["usage_date"].max()
    recent_start = latest - pd.Timedelta(days=config.RECENT_DAYS - 1)
    prior_start = recent_start - pd.Timedelta(days=config.BASELINE_DAYS)

    recent = df[df["usage_date"] >= recent_start]
    prior = df[(df["usage_date"] >= prior_start) & (df["usage_date"] < recent_start)]

    recent_rg = recent.groupby("resource_group")["cost"].sum()
    prior_rg = prior.groupby("resource_group")["cost"].sum()

    out = []

    for rg, cost in recent_rg.items():
        if rg in prior_rg.index:
            continue
        monthly = cost / config.RECENT_DAYS * 30
        if monthly >= config.MIN_ANOMALY_COST:
            out.append({
                "type": "new_spend", "dimension": "resource_group", "value": rg,
                "date": latest.date().isoformat(),
                "cost_so_far": round(float(cost), 2),
                "monthly_run_rate": round(float(monthly), 2),
                "impact": round(float(monthly), 2),
            })

    for rg, cost in prior_rg.items():
        if rg in recent_rg.index:
            continue
        monthly = cost / config.BASELINE_DAYS * 30
        if monthly >= config.MIN_ANOMALY_COST:
            out.append({
                "type": "stopped", "dimension": "resource_group", "value": rg,
                "date": latest.date().isoformat(),
                "prior_monthly": round(float(monthly), 2),
                "impact": round(float(monthly), 2),
            })

    return out
