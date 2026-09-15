# FinOps Agent Production Readme

## Prototype status
This repository is a functional prototype and proof of concept for an AI-assisted FinOps reporting workflow. It demonstrates the core logic, local dashboard, and mail flow, but it is not yet a production Azure AI deployment.

For an actual production implementation in Azure, the required items include:
- Azure AI Foundry or Azure OpenAI resource
- model deployment for summarization or assistant use
- Azure Key Vault for secrets
- Managed Identity for authentication
- Azure Storage or ADLS for data and reports
- Azure Function App or Container Apps for scheduled execution
- Application Insights / Log Analytics for monitoring
- RBAC and networking controls
- secure internal email integration via Microsoft Graph or a corporate relay
- internal recipient allowlist and governance review

---

## Purpose
The FinOps Agent is a local-first cloud cost reporting solution that turns Azure Cost Management exports into an explainable, actionable cost review. It detects anomalies, scores recommendations, renders a report, and can send the final output by email using a company-controlled sender identity.

---

## Architecture summary
The application is built around deterministic analysis logic in Python. The main workflow is:

1. Load Azure export data
2. Normalize and validate columns
3. Filter by business scope
4. Detect anomalies
5. Generate recommendations
6. Assemble a report
7. Optionally generate an AI narrative
8. Deliver the report by email or download it locally

Core files:
- app.py — Streamlit UI
- run.py — scheduled CLI entry point
- data.py — source data processing
- anomalies.py — anomaly logic
- rules.py — recommendation scoring
- report.py — report facts and narrative integration
- config.py — thresholds and environment values
- email_out.py — SMTP/email sending
- mcp_server.py — read-only MCP tool interface

---

## Production email configuration
For company production use, configure a dedicated FinOps mailbox, not a personal email account.

Recommended pattern:
- Sender: `finops-reports@company.com`
- Recipients: internal teams, distribution lists, or approved stakeholders
- SMTP host: Microsoft 365 or Exchange endpoint
- Credentials: dedicated mailbox or service identity

Environment variables:
```env
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=finops-reports@company.com
SMTP_PASSWORD=your-app-password
FINOPS_MAIL_FROM=finops-reports@company.com
FINOPS_MAIL_TO=finance-team@company.com,platform-ops@company.com
```

Strongly recommended security controls:
- internal-only recipient allowlist
- no personal email addresses
- managed secret store
- no external recipient abuse
- log delivery status, not content

---

## Local setup
```powershell
cd "C:\Repo\Finops Agent"
C:\Repo\.venv\Scripts\python.exe -m pip install -r requirements.txt
C:\Repo\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8504
```

Open:
- http://127.0.0.1:8504

---

## Production readiness checklist
- [.env] file contains valid SMTP settings
- Send-from address is a company-controlled mailbox
- Recipient list is internal only
- Export job is running and the data is fresh enough
- Data thresholds are configured appropriately
- Secret values are not committed to source control
- Report is reviewed by the relevant owners before acting on recommendations

---

## Recommended operating model
- Run report generation after the export lands
- Keep automatic send disabled unless the data is fresh
- Use an internal distribution list for stakeholder access
- Review false positives and false negatives as part of periodic tuning
- Treat model output as explanatory, not authoritative

---

## Summary
The FinOps Agent is a practical, explainable cost reporting tool for internal engineering and finance teams. It is designed to reduce noise, formalize cost review, and ensure the final report is grounded in deterministic calculations rather than model guesses.
