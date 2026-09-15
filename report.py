"""Assemble the report.

Order matters. Facts are computed first and frozen. The model is called last and
receives only computed facts, so it cannot change a number, only describe one.
If the model call fails the report still sends, with the tables and no prose.
That degradation path is deliberate: a report with numbers and no narrative is
useful, a report with narrative and no numbers is worthless.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import pandas as pd

import anomalies
import config
import data
import rules

log = logging.getLogger("finops.report")


class StaleData(RuntimeError):
    """The newest data is too old to report on."""


SYSTEM = """You are a FinOps analyst writing the summary for a cloud cost report
that goes to an engineering director.

You are given computed findings. Every figure in them is already verified. Your
job is the narrative, not the arithmetic.

Rules
- Never state a number that is not in the findings. Do not recompute, estimate or
  extrapolate anything.
- Open with the single most important thing, in one sentence.
- Then at most four short paragraphs. Lead each with what changed, not with a
  restatement of the data.
- Distinguish a spike from a step change. A spike is worth a look, a step change
  compounds every month until someone acts.
- Say what you would do next and who needs to do it.
- If the findings are thin, say the estate looks stable. Do not manufacture
  concern to fill space.
- Months flagged partial are incomplete. Never describe a partial month as a
  decline or a saving, and never compare it like for like against a full month.
- Plain language. No corporate filler, no preamble, no headings.
- Do not use dashes in your writing. Use commas or colons, or split the sentence.
"""


def build_facts(df: pd.DataFrame) -> dict:
    """Everything numeric, computed deterministically."""
    latest = df["usage_date"].max()
    age = (pd.Timestamp(date.today()) - latest).days
    if config.MAX_STALENESS_DAYS > 0 and age > config.MAX_STALENESS_DAYS:
        raise StaleData(
            f"Newest data is {latest.date()}, {age} days old. Threshold is "
            f"{config.MAX_STALENESS_DAYS}. Check the export job before trusting "
            "this report."
        )

    trimmed = df[df["usage_date"] <= latest - pd.Timedelta(
        days=config.TRAILING_LAG_DAYS)]
    window = trimmed[trimmed["usage_date"] >= trimmed["usage_date"].max()
                     - pd.Timedelta(days=29)]

    days = max(window["usage_date"].nunique(), 1)
    total = float(window["cost"].sum())
    untagged = float(window.loc[window["is_untagged"], "cost"].sum())

    by_app = (window.groupby("application")["cost"].sum()
              .sort_values(ascending=False).head(10))
    by_service = (window.groupby("service_name")["cost"].sum()
                  .sort_values(ascending=False).head(10))

    with_month = trimmed.assign(month=trimmed["usage_date"].dt.strftime("%Y-%m"))
    monthly = with_month.groupby("month")["cost"].sum().sort_index().tail(6)
    month_days = with_month.groupby("month")["usage_date"].nunique()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "currency": config.CURRENCY,
        "data_as_of": latest.date().isoformat(),
        "analysis_through": (latest - pd.Timedelta(
            days=config.TRAILING_LAG_DAYS)).date().isoformat(),
        "window_days": days,
        "window_total": round(total, 2),
        "daily_run_rate": round(total / days, 2),
        "monthly_run_rate": round(total / days * 30, 2),
        "tag_coverage_pct": round((1 - untagged / total) * 100, 1) if total else 0.0,
        "untagged_monthly": round(untagged / days * 30, 2),
        "top_applications": [
            {"name": k, "cost": round(float(v), 2)} for k, v in by_app.items()],
        "top_services": [
            {"name": k, "cost": round(float(v), 2)} for k, v in by_service.items()],
        # A partial month is not a decline. Flag it or every first-of-month
        # report announces a fictional saving.
        "monthly_totals": [
            {
                "month": k,
                "cost": round(float(v), 2),
                "days": int(month_days[k]),
                "partial": bool(month_days[k] < 28),
            }
            for k, v in monthly.items()
        ],
        # Pass the untrimmed frame: detect() owns the restatement trim. Passing
        # the already trimmed frame double trimmed it and shifted every window
        # by TRAILING_LAG_DAYS, which silently changed which findings appeared.
        "anomalies": anomalies.detect(df)[:12],
        "recommendations": rules.generate(trimmed)[:8],
    }


def narrate(facts: dict) -> str:
    """Ask the model for the summary. Returns empty string on any failure."""
    if not config.ANTHROPIC_API_KEY:
        log.warning("No API key set, sending report without narrative.")
        return ""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.NARRATE_MODEL,
            max_tokens=900,
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(facts, default=str)}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()
    except Exception:
        log.exception("Narration failed, sending report without it.")
        return ""


def _fmt(value: float, currency: str) -> str:
    return f"{value:,.0f} {currency}"


def to_markdown(facts: dict, summary: str) -> str:
    cur = facts["currency"]
    lines = [
        f"# Cloud Cost Report",
        "",
        f"Data as of **{facts['data_as_of']}**. Analysis through "
        f"**{facts['analysis_through']}**, excluding the most recent days while "
        "billing settles.",
        "",
        f"Monthly run rate **{_fmt(facts['monthly_run_rate'], cur)}** · "
        f"Tag coverage **{facts['tag_coverage_pct']}%**",
        "",
    ]

    if summary:
        lines += ["## Summary", "", summary, ""]

    recs = facts["recommendations"]
    lines += ["## Recommendations", ""]
    if recs:
        lines += ["| # | Finding | Est. monthly | Confidence | Effort | Action |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for i, r in enumerate(recs, 1):
            saving = _fmt(r["monthly_saving"], cur) if r["monthly_saving"] else "governance"
            lines.append(
                f"| {i} | {r['title']} | {saving} | {r['confidence']:.0%} | "
                f"{r['effort']} | {r['action']} |"
            )
        lines.append("")
        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. **{r['title']}** — {r['detail']}")
        lines.append("")
    else:
        lines += ["No findings above threshold this period.", ""]

    anom = facts["anomalies"]
    lines += ["## Anomalies", ""]
    if anom:
        lines += ["| Type | Scope | Detail | Impact |", "| --- | --- | --- | --- |"]
        for a in anom:
            if a["type"] == "spike":
                detail = (f"{_fmt(a['cost'], cur)} against a "
                          f"{_fmt(a['baseline'], cur)} baseline on {a['date']} "
                          f"(z {a['z_score']})")
                impact = _fmt(a["delta"], cur)
            elif a["type"] == "step_change":
                direction = "up" if a["change_pct"] > 0 else "down"
                detail = (f"daily run rate {direction} {abs(a['change_pct'])}%, "
                          f"{_fmt(a['daily_before'], cur)} to "
                          f"{_fmt(a['daily_now'], cur)}")
                impact = f"{_fmt(a['monthly_impact'], cur)}/mo"
            elif a["type"] == "new_spend":
                detail = "new resource group, not present in the prior window"
                impact = f"{_fmt(a['monthly_run_rate'], cur)}/mo"
            else:
                detail = "spend stopped, confirm this was intended"
                impact = f"{_fmt(a['prior_monthly'], cur)}/mo"
            lines.append(f"| {a['type']} | {a['value']} | {detail} | {impact} |")
        lines.append("")
    else:
        lines += ["Nothing outside the expected range.", ""]

    lines += ["## Top applications", "", f"| Application | Spend ({cur}) |",
              "| --- | --- |"]
    lines += [f"| {a['name']} | {a['cost']:,.0f} |" for a in facts["top_applications"]]
    lines += ["", "## Monthly trend", "", f"| Month | Spend ({cur}) | Days |",
              "| --- | --- | --- |"]
    for m in facts["monthly_totals"]:
        note = f"{m['days']} (partial)" if m["partial"] else str(m["days"])
        lines.append(f"| {m['month']} | {m['cost']:,.0f} | {note} |")
    lines += [
        "",
        "---",
        "",
        "Anomalies and recommendations are computed deterministically. The summary "
        "is written by a language model from those computed findings only. "
        "Recommendations are advisory and estimates are directional: confirm with "
        "the resource owner before acting.",
    ]
    return "\n".join(lines)


def to_html(markdown_text: str) -> str:
    """Minimal markdown to HTML for the email body.

    Deliberately hand rolled rather than pulling a dependency: email clients
    strip most CSS anyway, and this only needs to handle the subset produced
    above.
    """
    import html as html_mod
    import re

    out, in_table = [], False
    for raw in markdown_text.split("\n"):
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append('<table cellpadding="6" cellspacing="0" '
                           'style="border-collapse:collapse;font-size:13px;'
                           'width:100%">')
                in_table = True
                tag = "th"
            else:
                tag = "td"
            style = "border-bottom:1px solid #d5dbe1;text-align:left"
            row = "".join(
                f'<{tag} style="{style}">{_inline(c)}</{tag}>' for c in cells)
            out.append(f"<tr>{row}</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if not line:
            continue
        if line.startswith("# "):
            out.append(f'<h1 style="font-size:20px;margin:20px 0 8px">'
                       f'{_inline(line[2:])}</h1>')
        elif line.startswith("## "):
            out.append(f'<h2 style="font-size:15px;text-transform:uppercase;'
                       f'letter-spacing:.08em;color:#5a6b7a;margin:24px 0 8px">'
                       f'{_inline(line[3:])}</h2>')
        elif line == "---":
            out.append('<hr style="border:0;border-top:1px solid #d5dbe1;'
                       'margin:20px 0">')
        elif re.match(r"^\d+\.\s", line):
            out.append(f'<p style="margin:4px 0">{_inline(line)}</p>')
        else:
            out.append(f'<p style="margin:8px 0;line-height:1.5">'
                       f'{_inline(line)}</p>')
    if in_table:
        out.append("</table>")

    body = "\n".join(out)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'color:#16202b;max-width:760px;margin:0 auto;padding:8px">'
        f"{body}</div>"
    )


def _inline(text: str) -> str:
    import html as html_mod
    import re
    escaped = html_mod.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def generate() -> dict:
    """Load, compute, narrate, render. Returns everything the sender needs."""
    df = data.load_source(config.DATA_DIR)
    facts = build_facts(df)
    summary = narrate(facts)
    markdown_text = to_markdown(facts, summary)
    return {
        "facts": facts,
        "markdown": markdown_text,
        "html": to_html(markdown_text),
        "subject": (
            f"{config.MAIL_SUBJECT_PREFIX} Cloud cost report, "
            f"{facts['data_as_of']} · run rate "
            f"{_fmt(facts['monthly_run_rate'], facts['currency'])}/mo · "
            f"{len(facts['recommendations'])} recommendations"
        ),
    }
