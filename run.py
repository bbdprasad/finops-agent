#!/usr/bin/env python3
"""Generate the cost report, and optionally email it.

    python run.py                  # write to reports/, do not send
    python run.py --send           # write and email
    python run.py --facts          # print the computed findings as JSON
    python run.py --check-email    # validate SMTP config without generating

Default is not to send. Scheduled jobs that email by default are how people end
up mailing a broken report to a director at 07:00.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import config
import email_out
import report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("finops")


def main() -> int:
    parser = argparse.ArgumentParser(description="FinOps cost reporter")
    parser.add_argument("--send", action="store_true", help="email the report")
    parser.add_argument("--facts", action="store_true",
                        help="print computed findings as JSON and exit")
    parser.add_argument("--check-email", action="store_true",
                        help="validate email configuration and exit")
    args = parser.parse_args()

    if args.check_email:
        try:
            email_out.check()
        except email_out.NotConfigured as exc:
            log.error("%s", exc)
            return 1
        log.info("Email configured, %d recipient(s).", len(config.MAIL_TO))
        return 0

    # Fail before generating if the send would fail anyway.
    if args.send:
        try:
            email_out.check()
        except email_out.NotConfigured as exc:
            log.error("%s", exc)
            return 1

    try:
        result = report.generate()
    except report.StaleData as exc:
        # Non zero exit so cron's MAILTO alerts you. A stale report is worse
        # than no report, because it looks fine.
        log.error("Refusing to report: %s", exc)
        return 2
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    if args.facts:
        print(json.dumps(result["facts"], indent=2, default=str))
        return 0

    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = result["facts"]["data_as_of"]
    md_path = config.OUT_DIR / f"cost-report-{stamp}.md"
    html_path = config.OUT_DIR / f"cost-report-{stamp}.html"
    md_path.write_text(result["markdown"], encoding="utf-8")
    html_path.write_text(result["html"], encoding="utf-8")

    facts = result["facts"]
    log.info(
        "Report written to %s (%d recommendations, %d anomalies, run rate "
        "%.0f %s/mo)",
        md_path, len(facts["recommendations"]), len(facts["anomalies"]),
        facts["monthly_run_rate"], facts["currency"],
    )

    if args.send:
        email_out.send(result["subject"], result["html"], result["markdown"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
