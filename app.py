"""Local Streamlit interface for the FinOps cost reporter."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import config
import data
import email_out
import report


st.set_page_config(
    page_title="FinOps Control Room",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap');
    :root { --ink:#17221d; --muted:#65736b; --line:#dce5df; --paper:#f5f7f3; --lime:#d6f36a; --coral:#ff765c; }
    .stApp { background:var(--paper); color:var(--ink); }
    [data-testid="stHeader"] { background:transparent; }
    h1,h2,h3,p,div { font-family:'Space Grotesk', sans-serif; }
    h1 { letter-spacing:0; font-size:3rem; line-height:1; }
    .eyebrow { color:var(--coral); font-family:'DM Mono', monospace; font-size:.75rem; text-transform:uppercase; letter-spacing:.08em; }
    .subtle { color:var(--muted); }
    [data-testid="stMetric"] { background:#fff; border:1px solid var(--line); border-radius:6px; padding:16px; }
    [data-testid="stMetricLabel"] { color:var(--muted); }
    [data-testid="stMetricValue"] { color:var(--ink); }
    .finding { background:#fff; border-left:4px solid var(--coral); border-top:1px solid var(--line); border-right:1px solid var(--line); border-bottom:1px solid var(--line); border-radius:4px; padding:14px 16px; margin:8px 0; }
    .finding h4 { margin:0 0 6px; font-size:1rem; }
    .finding p { margin:4px 0; color:var(--muted); font-size:.9rem; }
    .finding .impact { color:var(--ink); font-family:'DM Mono', monospace; font-size:.82rem; }
    .stButton > button { border-radius:4px; border:1px solid var(--ink); font-weight:600; }
    </style>
    """,
    unsafe_allow_html=True,
)


def money(value: float, currency: str) -> str:
    return f"{value:,.0f} {currency}"


def finding_card(title: str, detail: str, impact: str, accent: str = "coral") -> None:
    border = "#d6f36a" if accent == "lime" else "#ff765c"
    st.markdown(
        f'<div class="finding" style="border-left-color:{border}">'
        f"<h4>{title}</h4><p>{detail}</p><div class=\"impact\">{impact}</div></div>",
        unsafe_allow_html=True,
    )


def analyze(uploaded_file):
    with tempfile.TemporaryDirectory(prefix="finops-") as directory:
        path = Path(directory) / uploaded_file.name
        path.write_bytes(uploaded_file.getvalue())
        frame = data.apply_scope(data.load(Path(directory)))
        facts = report.build_facts(frame)
        summary = report.narrate(facts)
        markdown = report.to_markdown(facts, summary)
        html = report.to_html(markdown)
    return facts, markdown, html, summary


st.markdown('<div class="eyebrow">cost intelligence / local workspace</div>', unsafe_allow_html=True)
st.title("FinOps Control Room")
st.markdown(
    '<p class="subtle">Upload an Azure Cost Management export. The numbers below are computed by deterministic rules; Claude is used only for the optional narrative.</p>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Input")
    uploaded = st.file_uploader("Azure cost export", type=["csv", "tsv"])
    st.caption("Azure UTF-8 and UTF-16 exports are supported.")
    analyze_clicked = st.button("Analyze costs", type="primary", use_container_width=True)
    st.divider()
    st.markdown("### Delivery")
    st.caption("Email remains opt-in. Configure SMTP in .env before sending.")
    check_email = st.button("Check email configuration", use_container_width=True)
    if check_email:
        try:
            email_out.check()
            st.success(f"SMTP ready for {len(__import__('config').MAIL_TO)} recipient(s).")
        except email_out.NotConfigured as exc:
            st.error(str(exc))

if analyze_clicked:
    if uploaded is None:
        st.warning("Choose an Azure cost export first.")
    else:
        try:
            with st.spinner("Normalizing costs and calculating findings..."):
                st.session_state.result = analyze(uploaded)
                st.session_state.filename = uploaded.name
        except Exception as exc:
            st.error(f"Analysis failed: {exc}")

result = st.session_state.get("result")
if result is None:
    st.info("Upload a cost export in the sidebar to begin.")
    st.stop()

facts, markdown, html, summary = result
currency = facts["currency"]

st.caption(f"Source: {st.session_state.get('filename', 'uploaded export')}  |  Data as of {facts['data_as_of']}  |  Analysis through {facts['analysis_through']}")
metric_cols = st.columns(5)
metric_cols[0].metric("Monthly run rate", money(facts["monthly_run_rate"], currency))
metric_cols[1].metric("Window total", money(facts["window_total"], currency))
metric_cols[2].metric("Tag coverage", f"{facts['tag_coverage_pct']}%")
metric_cols[3].metric("Anomalies", len(facts["anomalies"]))
metric_cols[4].metric("Recommendations", len(facts["recommendations"]))

if summary:
    st.markdown("### Executive readout")
    st.success(summary)
else:
    st.markdown("### Executive readout")
    if config.ANTHROPIC_API_KEY:
        st.info("Claude narrative could not be generated. The deterministic tables and findings remain complete.")
    else:
        st.info("Claude narrative is disabled. Copy .env.example to .env and set ANTHROPIC_API_KEY, then restart the GUI. The deterministic tables and findings remain complete.")

left, right = st.columns(2)
with left:
    st.markdown("### Anomalies")
    if not facts["anomalies"]:
        st.success("No anomalies crossed the configured thresholds.")
    for anomaly in facts["anomalies"]:
        if anomaly["type"] == "spike":
            detail = f"{money(anomaly['cost'], currency)} against {money(anomaly['baseline'], currency)} baseline on {anomaly['date']}"
            impact = f"One-day delta: {money(anomaly['delta'], currency)} · z={anomaly['z_score']}"
        elif anomaly["type"] == "step_change":
            direction = "up" if anomaly["change_pct"] > 0 else "down"
            detail = f"Daily run rate {direction} {abs(anomaly['change_pct'])}% from {money(anomaly['daily_before'], currency)} to {money(anomaly['daily_now'], currency)}"
            impact = f"Monthly impact: {money(abs(anomaly['monthly_impact']), currency)}"
        elif anomaly["type"] == "new_spend":
            detail = "Resource group was not present in the prior comparison window."
            impact = f"Monthly run rate: {money(anomaly['monthly_run_rate'], currency)}"
        else:
            detail = "Spend disappeared from the recent comparison window."
            impact = f"Prior monthly spend: {money(anomaly['prior_monthly'], currency)}"
        finding_card(f"{anomaly['type']} · {anomaly['value']}", detail, impact)

with right:
    st.markdown("### Recommendations")
    if not facts["recommendations"]:
        st.success("No recommendations crossed the configured thresholds.")
    for recommendation in facts["recommendations"]:
        saving = recommendation["monthly_saving"]
        estimate = money(saving, currency) if saving else "Governance finding"
        detail = f"Confidence {recommendation['confidence']:.0%} · Effort {recommendation['effort']} · Score {recommendation['score']:,.0f}"
        finding_card(recommendation["title"], recommendation["action"], f"{estimate} · {detail}", "lime")

st.markdown("### Spend shape")
chart_data = pd.DataFrame(facts["top_services"]).set_index("name")
st.bar_chart(chart_data, y="cost", color="#ff765c")

st.markdown("### Report")
download_cols = st.columns([1, 1, 1, 3])
download_cols[0].download_button("Download Markdown", markdown, file_name=f"finops-report-{facts['data_as_of']}.md", mime="text/markdown", use_container_width=True)
download_cols[1].download_button("Download HTML", html, file_name=f"finops-report-{facts['data_as_of']}.html", mime="text/html", use_container_width=True)
if download_cols[2].button("Send email", use_container_width=True):
    try:
        email_out.send(
            f"FinOps report, {facts['data_as_of']}", html, markdown
        )
        st.success("Report sent.")
    except email_out.NotConfigured as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Email failed: {exc}")

with st.expander("View generated Markdown"):
    st.code(markdown, language="markdown")
