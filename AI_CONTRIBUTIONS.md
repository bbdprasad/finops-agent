# AI Contributions and Attribution

This project was developed with assistance from **GitHub Copilot** and uses
**Anthropic Claude** as an optional report-narration model.

## GitHub Copilot

GitHub Copilot was used as a coding assistant to:

- inspect the existing FinOps codebase and identify broken first-run paths;
- fix local imports, project-relative data paths, and sample-data setup;
- add UTF-16 Azure export support;
- create the local Streamlit GUI;
- add setup, scheduling, architecture, and AI-attribution documentation;
- run local validation commands and interpret diagnostics.

The resulting source code, thresholds, and design decisions should be reviewed
and owned by the project team before production deployment.

## Anthropic Claude

When `ANTHROPIC_API_KEY` is configured, Claude receives the already computed
facts from `report.py` and writes the executive summary. Claude is explicitly
not responsible for anomaly detection, recommendation ranking, savings
arithmetic, or threshold decisions. The report remains useful when the Claude
call is unavailable because the deterministic tables are still rendered.

## Human responsibility

The project team remains responsible for validating Azure export semantics,
SMTP permissions, recipient lists, cost thresholds, data privacy, and all cloud
cost actions. Estimated savings are directional and require confirmation from
the relevant service owner.