"""Send one campaign to subscribed recipients, then stop."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from mailbot.config import Settings
from mailbot.message import build_message, merge_context
from mailbot.pace import next_delay
from mailbot.render import TemplateError, check_templates, with_required_footer
from mailbot.smtp_client import AuthFailed, MailerError, RecipientRejected, TemporaryFailure
from mailbot.store import Store, Subscriber, utcnow


Log = Callable[[str], None]
Sleep = Callable[[float], None]
Now = Callable


@dataclass
class SendReport:
    planned: int = 0
    sent: int = 0
    failed: int = 0
    stopped: str = ""


class CampaignError(Exception):
    """The campaign cannot be sent as it stands."""


def load_template(path) -> str:
    if not path.is_file():
        raise CampaignError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def prepare_campaign(
    store: Store,
    settings: Settings,
    *,
    name: str,
    subject: str,
    html_template: str,
    text_template: str,
    limit: int | None,
) -> tuple[int, list[Subscriber]]:
    if not name.strip():
        raise CampaignError("Campaign name is required.")
    if not subject.strip():
        raise CampaignError("Subject is required.")
    html_ready = with_required_footer(html_template, html=True)
    text_ready = with_required_footer(text_template, html=False)
    campaign_id = store.upsert_campaign(name.strip(), subject, html_ready, text_ready)
    recipients = store.pending_recipients(campaign_id)
    if limit is not None:
        if limit < 1:
            raise CampaignError("--limit must be at least 1.")
        recipients = recipients[:limit]
    problems = check_templates(
        [subject, html_ready, text_ready],
        [
            (person.email, _context_keys(settings, person, subject))
            for person in recipients
        ],
    )
    if problems:
        preview = "\n".join(problems[:20])
        extra = f"\n... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise CampaignError(f"Template placeholders do not match the list:\n{preview}{extra}")
    return campaign_id, recipients


def _context_keys(settings: Settings, person: Subscriber, subject: str) -> dict[str, str]:
    return merge_context(settings, person, subject=subject)


def send_campaign(
    store: Store,
    settings: Settings,
    mailer,
    *,
    campaign_id: int,
    recipients: list[Subscriber],
    subject: str,
    html_template: str,
    text_template: str,
    log: Log = print,
    sleep: Sleep = time.sleep,
    now: Now = utcnow,
) -> SendReport:
    report = SendReport(planned=len(recipients))
    for index, person in enumerate(recipients, start=1):
        delay = next_delay(
            store.recent_sent(now() - timedelta(hours=1)),
            now(),
            settings.hourly_limit,
        )
        if delay >= 1:
            log(
                f"Waiting {math.ceil(delay)}s to stay within {settings.hourly_limit} messages per hour."
            )
            sleep(delay)
        try:
            message = build_message(
                settings,
                person,
                subject_template=subject,
                html_template=html_template,
                text_template=text_template,
            )
            _deliver(mailer, message, person.email, sleep, log)
        except TemplateError as exc:
            raise CampaignError(str(exc)) from exc
        except RecipientRejected as exc:
            store.record_delivery(
                recipient=person.email,
                status="failed",
                campaign_id=campaign_id,
                subscriber_id=person.id,
                error=str(exc),
                sent_at=now(),
            )
            report.failed += 1
            log(f"failed {index}/{report.planned} {person.email}: {exc}")
            continue
        except TemporaryFailure as exc:
            store.record_delivery(
                recipient=person.email,
                status="failed",
                campaign_id=campaign_id,
                subscriber_id=person.id,
                error=str(exc),
                sent_at=now(),
            )
            report.failed += 1
            report.stopped = str(exc)
            log(f"Stopped after a temporary mail error for {person.email}: {exc}")
            return report
        except AuthFailed:
            raise
        except MailerError as exc:
            report.stopped = str(exc)
            log(f"Stopped: {exc}")
            return report

        store.record_delivery(
            recipient=person.email,
            status="sent",
            campaign_id=campaign_id,
            subscriber_id=person.id,
            message_id=message["Message-ID"],
            sent_at=now(),
        )
        report.sent += 1
        log(f"sent {index}/{report.planned} {person.email}")
    return report


def _deliver(mailer, message, recipient: str, sleep: Sleep, log: Log) -> None:
    waits = (0, 30, 60)
    last: TemporaryFailure | None = None
    for pause in waits:
        if pause:
            log(f"Mail server asked for a retry. Waiting {pause}s.")
            sleep(pause)
        try:
            mailer.send(message, recipient)
            return
        except TemporaryFailure as exc:
            last = exc
    assert last is not None
    raise last
