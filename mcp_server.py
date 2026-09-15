"""MCP interface for the local FinOps analysis engine.

The server exposes read-only analysis tools. It reuses the same source adapter,
anomaly detectors, recommendation rules, and report builder as the GUI and CLI.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

import config
import data
import report

mcp = MCPServer(name="supply-chain-finops", version="0.1.0")


def _facts() -> dict[str, Any]:
    frame = data.load_source(config.DATA_DIR)
    return report.build_facts(frame)


def _impact(finding: dict[str, Any]) -> float:
    return float(finding.get("impact", finding.get("score", 0)))


@mcp.tool(description="Return the current Supply Chain cost summary.")
def get_cost_summary() -> dict[str, Any]:
    """Return the current Supply Chain cost summary.

    Includes the analysis dates, monthly run rate, tag coverage, top services,
    anomaly count, and recommendation count. Values are computed from the
    configured local, Azure Blob, or Databricks source.
    """
    facts = _facts()
    return {
        "business_scope": config.BUSINESS_SCOPE,
        "data_source": config.DATA_SOURCE,
        "data_as_of": facts["data_as_of"],
        "analysis_through": facts["analysis_through"],
        "window_days": facts["window_days"],
        "currency": facts["currency"],
        "window_total": facts["window_total"],
        "monthly_run_rate": facts["monthly_run_rate"],
        "tag_coverage_pct": facts["tag_coverage_pct"],
        "anomaly_count": len(facts["anomalies"]),
        "recommendation_count": len(facts["recommendations"]),
        "top_services": facts["top_services"],
    }


@mcp.tool(description="List detected Supply Chain cost anomalies ordered by impact.")
def list_anomalies(limit: int = 12) -> list[dict[str, Any]]:
    """List detected Supply Chain cost anomalies ordered by impact.

    Args:
        limit: Maximum number of findings to return, from 1 through 50.
    """
    limit = max(1, min(limit, 50))
    findings = _facts()["anomalies"]
    return sorted(findings, key=_impact, reverse=True)[:limit]


@mcp.tool(description="List scored Supply Chain cost recommendations ordered by score.")
def list_recommendations(limit: int = 8) -> list[dict[str, Any]]:
    """List scored Supply Chain cost recommendations ordered by score.

    Args:
        limit: Maximum number of recommendations to return, from 1 through 50.
    """
    limit = max(1, min(limit, 50))
    findings = _facts()["recommendations"]
    return sorted(findings, key=lambda item: float(item.get("score", 0)), reverse=True)[:limit]


@mcp.tool(description="Generate the current report without sending email.")
def generate_report(include_narrative: bool = False) -> dict[str, Any]:
    """Generate the current report as Markdown and HTML without sending email.

    Args:
        include_narrative: Ask Claude for the optional executive summary. Numeric
            findings always come from deterministic Python calculations.
    """
    facts = _facts()
    summary = report.narrate(facts) if include_narrative else ""
    markdown = report.to_markdown(facts, summary)
    return {
        "subject": (
            f"{config.MAIL_SUBJECT_PREFIX} Cloud cost report, "
            f"{facts['data_as_of']}"
        ),
        "data_as_of": facts["data_as_of"],
        "business_scope": config.BUSINESS_SCOPE,
        "summary": summary,
        "markdown": markdown,
        "html": report.to_html(markdown),
    }


if __name__ == "__main__":
    mcp.run()
