# FinOps Cost Reporter

Point it at cost exports, it emails a recommendations and anomalies report on a
schedule. Minimal setup: no database, no server, no cloud API access.

## The one design decision that matters

Anomalies and recommendations are computed in **Python, deterministically**. The
language model receives the finished findings and writes the summary paragraph.
It never computes, ranks or estimates anything.

Consequences worth understanding:

- Same data in, same findings out. Every time. That is not achievable if a model
  is doing the ranking, even at temperature zero.
- Every number in the email is defensible. When someone asks why an item is
  ranked first, there is a scoring function to point at.
- If the model call fails, the report still sends with the tables and no prose.
  A report with numbers and no narrative is useful. The reverse is worthless.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in, never commit
python make_sample.py         # synthetic data with known findings
set -a && . ./.env && set +a

python run.py                 # write to reports/, do not send
python run.py --send          # write and email
python run.py --facts         # print computed findings as JSON
python run.py --check-email   # validate SMTP config only
streamlit run app.py          # open the local GUI
```

Drop real Azure Cost Management CSVs into `data/`. Common Azure and FOCUS column
names are mapped automatically, and application, environment, cost centre and
owner are lifted out of the Tags blob.

The scheduled runner can also read from Azure Blob Storage or a Databricks SQL
view. Set `FINOPS_DATA_SOURCE` to `blob` or `databricks`; both modes default to
the last two years and filter the configured business scope to **Supply Chain**.
See `.env.example` and [ARCHITECTURE.md](ARCHITECTURE.md) for the required
settings and production design.

Default is **not** to send. Scheduled jobs that email by default are how a broken
report reaches a director at 07:00.

## Scheduling

See `crontab.example`. Two things it gets right that are easy to miss: cron does
not load your shell profile, so the env file is sourced explicitly with absolute
paths, and `MAILTO` is set so a non-zero exit reaches you rather than failing
silently.

On Windows, create a weekly Task Scheduler task with:

- **Trigger:** weekly, Monday morning, after the cost export has landed
- **Program:** `C:\path\to\python.exe` from the virtual environment
- **Arguments:** `run.py --send`
- **Start in:** `C:\path\to\Finops Agent`

The runner loads `.env` from the project directory automatically. Keep `.env`
out of source control and set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`,
`FINOPS_MAIL_FROM`, and `FINOPS_MAIL_TO` before enabling `--send`. Test the
configuration with `python run.py --check-email`, then run `python run.py` once
without `--send` before scheduling delivery.

## Local GUI

Install the dependencies and start the local interface:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the URL printed by Streamlit, normally `http://localhost:8501`. Upload an
Azure Cost Management CSV or TSV, select **Analyze costs**, and use the report
buttons to download Markdown or HTML. The **Send email** button is deliberately
explicit and uses the same SMTP configuration as the command-line runner.

## MCP layer

The local MCP server exposes the FinOps engine to Claude, GitHub Copilot, or
another MCP-compatible client:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mcp_server.py
```

The server uses stdio and is configured for VS Code in `.vscode/mcp.json`. It
provides read-only tools for cost summary, anomaly findings, scored
recommendations, and report generation. MCP does not send email or change Azure
resources. The existing CLI remains responsible for scheduled weekly delivery.

The included local VS Code configuration points to `data/supply-chain/` and
filters the configured `business_unit` field to **Supply Chain**. Replace the
local export in that folder when you want to analyze a newer file.

## What it detects

### Anomalies, four kinds

| Type | Meaning | What to do |
| --- | --- | --- |
| `spike` | one day well outside its baseline | look, rarely act |
| `step_change` | sustained shift in run rate | act, it compounds monthly |
| `new_spend` | resource group not in the prior window | confirm it was intended |
| `stopped` | spend fell to zero | successful decommission, or a broken pipeline |

Three statistical choices, each of which was necessary:

**Median and MAD, not mean and standard deviation.** Cost data is spiky. One
large day inflates the standard deviation enough to hide the next one, so a
mean-based z-score gets progressively blinder the more anomalies you have. Median
absolute deviation does not move when a single point does.

**Same weekday comparison.** Cloud cost has strong weekly seasonality: non
production idles at weekends, batch jobs land on schedules. Compare Monday to
other Mondays or every Monday is an anomaly.

**Multiple grains.** Detection runs per application, per service, and per
application-service pair, then merges. A 45 percent jump in one service inside a
four service application is about an 11 percent move at application level, under
any sensible threshold. Detect only at application level and you systematically
miss the service level shifts that are the actual cause. This was not a
hypothetical: the planted test case was invisible until the finer grain was added.

**Two horizons.** Seven days against the prior twenty eight catches recent steps.
Thirty days against the prior thirty catches sustained shifts the short window has
already absorbed into its own baseline. A step from three weeks ago is half inside
the short baseline and measures as diluted.

### Recommendations, five rules

`nonprod_no_weekend_shutdown`, `estate_nonprod_ratio`, `nonprod_outlier`,
`tag_coverage_below_target`, `service_growth_outlier`, `meter_concentration`.

Scored deterministically:

```
score = estimated_monthly_saving × confidence × effort_factor × risk_factor
```

**Be honest about what cost data alone can tell you.** Without utilisation
metrics or an Advisor feed you cannot detect an idle VM. What billing data reveals
is *structural* waste: patterns in the shape of spend that indicate a problem
regardless of utilisation. That is usually where the larger money is anyway.
Rightsizing one VM saves tens. A weekend shutdown that was never implemented saves
thousands.

**A rule that fires on everything conveys nothing.** The first version of the non
production rule flagged all four applications in the test estate. Technically
correct, operationally useless. It now reports the estate ratio once as a single
finding, and flags individual applications only when they sit well above the estate
baseline. One number plus the genuine exceptions, instead of a wall of rows.

## Thresholds

These decide whether the report is read or filtered to a folder. Every false
positive costs credibility, and a report nobody trusts is worse than no report.

| Setting | Default | Purpose |
| --- | --- | --- |
| `FINOPS_Z_THRESHOLD` | 3.5 | robust z above which a day is a spike |
| `FINOPS_MIN_ANOMALY_COST` | 500 | absolute floor for single day findings |
| `FINOPS_MIN_MONTHLY_IMPACT` | 5000 | higher floor for monthly impact findings |
| `FINOPS_STEP_CHANGE_PCT` | 0.25 | minimum move for a step change |
| `FINOPS_TRAILING_LAG_DAYS` | 2 | days excluded while billing settles |
| `FINOPS_MAX_STALENESS_DAYS` | 3 | refuse to report beyond this |

The absolute floors matter more than the statistics. A 400 percent jump on 12 DKK
is arithmetic, not a finding, and separate floors are needed because 500 DKK on a
single day is significant while 500 DKK a month on a 400k estate is rounding.

## Failure modes handled

**Silent staleness.** The defining failure of a cost report. The export job fails,
nobody notices, and the report keeps arriving fluently with last week's data. It
never says it does not know. So: every report states its data as of date, and the
job exits non-zero and refuses to send beyond the staleness threshold.

**Restatement tail.** Billing revises the last several days as meters settle. The
newest days are trimmed, and the report says so, otherwise settling data reads as
spend collapsing.

**Partial months.** A month twenty days in looks like a thirty percent decline.
The monthly trend table marks day counts and flags partial months, and the model
is instructed never to describe one as a saving.

**Spike contaminating a step change.** A single 9x day inside the recent window
drags a mean past the step change threshold. Both windows use the median, so a
step change means a shift in the typical day.

**Duplicate findings across grains.** Detecting at three grains reports the same
event up to three times. Dedupe is greedy across everything already kept for the
same type and date, preferring the more specific scope, because it names where to
look.

## Known limitations

- No utilisation data, so no idle resource or rightsizing detection
- No Advisor feed, so no provider recommendations
- Daily grain, so out of hours waste is inferred from weekend patterns rather than
  observed hourly
- Savings estimates are directional and confidence weighted, not commitments
- No recommendation lifecycle: nothing tracks accepted, implemented or verified
  saved. Identified against realised is the number that justifies the tool, and
  it is not here yet
- Single currency, no FX conversion
- Amortised against actual depends on which export you feed it. Use amortised for
  trend reporting, and be consistent

## Next, in order of payback

1. **Recommendation outcome tracking.** Accepted, rejected with reason,
   implemented, verified saved. Without it this is a detection engine with no
   feedback loop, and detection was never the bottleneck.
2. **A golden test set.** Ten to fifteen findings you have verified by hand,
   asserted on every threshold change. Tuning without it is guessing.
3. **Advisor API.** A second table and a rule that joins provider
   recommendations against cost.
4. **Hourly data for the top few applications.** Turns inferred out of hours waste
   into observed.
