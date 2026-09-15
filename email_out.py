"""Email delivery over SMTP with STARTTLS.

Credentials come from the environment. For anything beyond a personal laptop use
a key vault and a dedicated service account rather than a personal mailbox, and
prefer your provider's transactional API over SMTP.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import config

log = logging.getLogger("finops.email")


class NotConfigured(RuntimeError):
    """Email settings are incomplete."""


def check() -> None:
    missing = [name for name, value in (
        ("SMTP_HOST", config.SMTP_HOST),
        ("SMTP_USER", config.SMTP_USER),
        ("SMTP_PASSWORD", config.SMTP_PASSWORD),
        ("FINOPS_MAIL_FROM", config.MAIL_FROM),
        ("FINOPS_MAIL_TO", config.MAIL_TO),
    ) if not value]
    if missing:
        raise NotConfigured(f"Missing environment variables: {', '.join(missing)}")


def send(subject: str, html: str, markdown_text: str) -> None:
    """Send the report. The markdown goes along as an attachment so the reader can
    keep or forward it without the email formatting."""
    check()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.MAIL_FROM
    message["To"] = ", ".join(config.MAIL_TO)
    message.set_content(markdown_text)          # plain text fallback
    message.add_alternative(html, subtype="html")
    message.add_attachment(
        markdown_text.encode("utf-8"),
        maintype="text", subtype="markdown",
        filename="cloud-cost-report.md",
    )

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(message)

    # Recipients are logged, message content is not.
    log.info("Report sent to %d recipient(s)", len(config.MAIL_TO))
