"""Local HireLens Mail web app: compose UI plus unsubscribe pages."""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import select
import socket
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from mailbot.config import ConfigError, Settings, load_settings, project_root, settings_with_overrides
from mailbot.hiring_templates import (
    SUBJECT as HIRING_SUBJECT,
    get_template,
    list_templates,
    pick_random_template,
    write_template_files,
)
from mailbot.message import build_compose_message, resolve_body_format
from mailbot.send_job import (
    clear_job,
    job_path_for,
    load_job,
    mark_done,
    mark_paused,
    mark_sent,
    recipients_from_job,
    resumable_job,
    start_job,
)
from mailbot.smtp_client import AuthFailed, MailerError, RecipientRejected, SmtpMailer, TemporaryFailure
from mailbot.store import Store, normalize_email, normalize_header, valid_email


TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,200}$")
RECIPIENT_RE = re.compile(r"^(?:([^<>]*?)\s*)?(?:<([^>]+)>|([^\s,;]+))$")
MAX_BODY = 2_000_000
MAX_IDLE_SECONDS = 3600
LOG_LIMIT = 200
# Retry temporary SMTP codes (421/450/451/…) before stopping a bulk run.
TEMPORARY_RETRY_WAITS = (0, 30, 60)
GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
OUTLOOK_DOMAINS = frozenset(
    {
        "outlook.com",
        "outlook.co.uk",
        "outlook.fr",
        "outlook.de",
        "outlook.es",
        "outlook.it",
        "outlook.ca",
        "hotmail.com",
        "hotmail.co.uk",
        "hotmail.fr",
        "hotmail.de",
        "hotmail.es",
        "hotmail.it",
        "hotmail.ca",
        "live.com",
        "live.co.uk",
        "msn.com",
    }
)
_LOG_LOCK = threading.Lock()
_LOG_LINES: deque[str] = deque(maxlen=LOG_LIMIT)


def is_gmail_or_outlook(email: str) -> bool:
    address = normalize_email(email)
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[-1]
    return domain in GMAIL_DOMAINS or domain in OUTLOOK_DOMAINS


def filter_gmail_outlook(
    people: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    kept = [(email, name) for email, name in people if is_gmail_or_outlook(email)]
    return kept, len(people) - len(kept)


def append_log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    with _LOG_LOCK:
        _LOG_LINES.append(line)
    print(line)


def recent_logs(limit: int = 80) -> list[str]:
    with _LOG_LOCK:
        lines = list(_LOG_LINES)
    return lines[-max(1, min(limit, LOG_LIMIT)) :]


class AppError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def default_idle_seconds(settings: Settings) -> int:
    """Suggested pause from the hourly mailbox cap."""
    return max(0, min(MAX_IDLE_SECONDS, int(3600 / settings.hourly_limit)))


def parse_idle_seconds(value: object, *, fallback: int = 0) -> int:
    if value is None or value == "":
        return fallback
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError("Idle time must be a number of seconds.") from exc
    if seconds < 0:
        raise AppError("Idle time cannot be negative.")
    if seconds > MAX_IDLE_SECONDS:
        raise AppError(f"Idle time cannot be more than {MAX_IDLE_SECONDS} seconds.")
    return int(seconds)


def peer_closed(sock: socket.socket | None) -> bool:
    """True when the browser closed the HTTP connection (Stop / tab close)."""
    if sock is None:
        return True
    try:
        readable, _, exceptional = select.select([sock], [], [sock], 0)
        if exceptional:
            return True
        if not readable:
            return False
        data = sock.recv(1, socket.MSG_PEEK)
        return data == b""
    except OSError:
        return True


def wait_idle(
    seconds: float,
    sleep: Callable[[float], None],
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Sleep for idle pacing. Returns True if stop was requested during the wait."""
    if seconds <= 0:
        return bool(should_stop and should_stop())
    if should_stop is None:
        sleep(seconds)
        return False
    remaining = float(seconds)
    while remaining > 0:
        if should_stop():
            return True
        step = min(0.25, remaining)
        sleep(step)
        remaining -= step
    return should_stop()


def deliver_with_retry(
    mailer,
    message,
    recipient: str,
    *,
    sleep: Callable[[float], None],
    emit: Callable[[dict], None],
    should_stop: Callable[[], bool] | None = None,
    waits: tuple[int, ...] = TEMPORARY_RETRY_WAITS,
) -> None:
    """Send one message. Retry temporary SMTP failures a few times, then raise."""
    last: TemporaryFailure | None = None
    for attempt, pause in enumerate(waits):
        if should_stop and should_stop():
            raise TemporaryFailure("Stopped before retry completed.")
        if pause:
            emit(
                {
                    "type": "waiting",
                    "email": recipient,
                    "idle_seconds": pause,
                    "message": (
                        f"Temporary mail error for {recipient}. "
                        f"Retry {attempt}/{len(waits) - 1} in {pause}s."
                    ),
                }
            )
            if wait_idle(pause, sleep, should_stop):
                raise TemporaryFailure("Stopped during temporary-error retry.")
        try:
            mailer.send(message, recipient)
            return
        except TemporaryFailure as exc:
            last = exc
            emit(
                {
                    "type": "failed",
                    "email": recipient,
                    "error": str(exc),
                    "message": f"Temporary mail error for {recipient}: {exc}",
                }
            )
    assert last is not None
    raise last


def format_recipient_line(email: str, name: str = "") -> str:
    address = normalize_email(email)
    display = (name or "").strip()
    if display:
        return f"{display} <{address}>"
    return address


def merge_recipients(
    existing: list[tuple[str, str]],
    incoming: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for address, name in existing + incoming:
        key = normalize_email(address)
        if key in seen:
            continue
        seen.add(key)
        merged.append((key, name.strip()))
    return merged


def parse_recipients(raw: str, *, require_gmail_outlook: bool = True) -> list[tuple[str, str]]:
    people: list[tuple[str, str]] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\n,;]+", raw or ""):
        piece = chunk.strip()
        if not piece:
            continue
        match = RECIPIENT_RE.match(piece)
        if match is None:
            raise AppError(f"Could not parse recipient: {piece}")
        name = (match.group(1) or "").strip().strip('"')
        address = normalize_email(match.group(2) or match.group(3) or "")
        if not valid_email(address):
            raise AppError(f"{piece} is not a valid email address.")
        if address in seen:
            continue
        seen.add(address)
        people.append((address, name))
    if not people:
        raise AppError("Add at least one recipient in the To field.")
    if require_gmail_outlook:
        kept, skipped = filter_gmail_outlook(people)
        if not kept:
            raise AppError(
                "No Gmail or Outlook addresses found. Only gmail.com / googlemail.com "
                "and Outlook/Hotmail/Live/MSN addresses are sent."
            )
        return kept
    return people


def _split_address(value: str) -> tuple[str, str]:
    """Accept bare emails or `Name <email@domain>` values."""
    piece = (value or "").strip()
    match = RECIPIENT_RE.match(piece)
    if match is None:
        return normalize_email(piece), ""
    name = (match.group(1) or "").strip().strip('"')
    address = normalize_email(match.group(2) or match.group(3) or "")
    return address, name


def _recipient_from_mapping(row: dict) -> tuple[str, str] | None:
    mapped = {normalize_header(str(key)): str(value or "").strip() for key, value in row.items() if key is not None}
    email = mapped.get("email", "")
    if not email:
        for key in ("mail", "address", "recipient"):
            if mapped.get(key):
                email = mapped[key]
                break
    if not email:
        return None
    address, embedded_name = _split_address(email)
    name = mapped.get("name") or mapped.get("full_name") or embedded_name
    if not name:
        first = mapped.get("first_name", "")
        last = mapped.get("last_name", "")
        name = f"{first} {last}".strip()
    if not valid_email(address):
        raise AppError(f"{email} is not a valid email address.")
    return address, name


def parse_recipients_csv(content: str) -> list[tuple[str, str]]:
    text = content.lstrip("\ufeff").strip()
    if not text:
        raise AppError("CSV file is empty.")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError("CSV needs a header row with an email column.")
    headers = {normalize_header(name) for name in reader.fieldnames if name}
    if not headers.intersection({"email", "mail", "address", "recipient", "e_mail", "email_address"}):
        # Allow a single-column CSV with no useful header by falling back below.
        if len(reader.fieldnames) == 1:
            return parse_recipients_lines(text)
        raise AppError("CSV needs an email column (email, mail, address, or recipient).")

    people: list[tuple[str, str]] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for index, row in enumerate(reader, start=2):
        try:
            parsed = _recipient_from_mapping(row)
        except AppError as exc:
            invalid.append(f"line {index}: {exc}")
            continue
        if parsed is None:
            continue
        address, name = parsed
        if address in seen:
            continue
        seen.add(address)
        people.append((address, name))
    if invalid and not people:
        raise AppError(invalid[0])
    if not people:
        raise AppError("No email addresses found in the CSV.")
    return people


def parse_recipients_lines(
    content: str,
    *,
    require_gmail_outlook: bool = True,
) -> list[tuple[str, str]]:
    """Parse a simple one-address-per-line file, with or without a header."""
    lines = [line.strip() for line in content.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        raise AppError("File is empty.")
    if normalize_header(lines[0]) in {"email", "mail", "address", "recipient", "e_mail", "email_address"}:
        lines = lines[1:]
    return parse_recipients("\n".join(lines), require_gmail_outlook=require_gmail_outlook)


def parse_recipients_json(content: str) -> list[tuple[str, str]]:
    text = content.strip()
    if not text:
        raise AppError("JSON file is empty.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(f"Invalid JSON: {exc.msg}") from exc

    rows: list[object]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("recipients", "emails", "contacts", "people", "to", "addresses"):
            if key in data and isinstance(data[key], list):
                rows = data[key]
                break
        else:
            if "email" in data:
                rows = [data]
            else:
                raise AppError(
                    "JSON must be an array, or an object with recipients/emails/contacts."
                )
    else:
        raise AppError("JSON must be an array or object.")

    people: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows, start=1):
        if isinstance(item, str):
            address = normalize_email(item)
            if not valid_email(address):
                raise AppError(f"Item {index}: {item} is not a valid email address.")
            parsed = (address, "")
        elif isinstance(item, dict):
            parsed = _recipient_from_mapping(item)
            if parsed is None:
                raise AppError(f"Item {index}: missing email.")
        else:
            raise AppError(f"Item {index}: expected a string or object.")
        address, name = parsed
        if address in seen:
            continue
        seen.add(address)
        people.append((address, name))
    if not people:
        raise AppError("No email addresses found in the JSON.")
    return people


def parse_recipient_upload(filename: str, content: str) -> dict:
    name = (filename or "upload").lower()
    if name.endswith(".json"):
        people = parse_recipients_json(content)
        source = "json"
    elif name.endswith(".txt"):
        people = parse_recipients_lines(content, require_gmail_outlook=False)
        source = "txt"
    elif name.endswith(".csv"):
        try:
            people = parse_recipients_csv(content)
        except AppError:
            people = parse_recipients_lines(content, require_gmail_outlook=False)
        source = "csv"
    else:
        stripped = content.lstrip()
        if stripped.startswith("[") or stripped.startswith("{"):
            people = parse_recipients_json(content)
            source = "json"
        else:
            try:
                people = parse_recipients_csv(content)
            except AppError:
                people = parse_recipients_lines(content, require_gmail_outlook=False)
            source = "csv"

    kept, skipped = filter_gmail_outlook(people)
    if not kept:
        raise AppError(
            "No Gmail or Outlook addresses found in the file. "
            "Only gmail.com / googlemail.com and Outlook/Hotmail/Live/MSN addresses are kept."
        )
    lines = [format_recipient_line(email, display) for email, display in kept]
    message = f"Loaded {len(kept)} Gmail/Outlook address(es) from {source.upper()}."
    if skipped:
        message += f" Skipped {skipped} other domain(s)."
    return {
        "ok": True,
        "source": source,
        "count": len(kept),
        "skipped": skipped,
        "recipients": [{"email": email, "name": display} for email, display in kept],
        "to_text": "\n".join(lines),
        "message": message,
    }


def parse_body_format(value: object) -> str:
    fmt = str(value or "auto").strip().lower()
    if fmt not in {"plain", "html", "auto"}:
        raise AppError("Content format must be plain, html, or auto.")
    return fmt


def parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise AppError("Random style must be true or false.")


def send_compose(
    settings: Settings,
    *,
    recipients: list[tuple[str, str]],
    subject: str,
    body: str,
    body_format: str = "plain",
    random_style: bool = False,
    idle_seconds: int = 0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] | None = None,
    on_progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    job_path: Path | None = None,
    resume_offset: int = 0,
    replace_job: bool = True,
    rng=None,
) -> dict:
    subject = subject.strip()
    body = body.strip()
    random_style = parse_bool(random_style)
    requested_format = parse_body_format(body_format)
    if random_style:
        body_format = "html"
        if not subject:
            subject = HIRING_SUBJECT
    else:
        body_format = resolve_body_format(body, requested_format)
    if not subject:
        raise AppError("Subject is required.")
    if not random_style and not body:
        raise AppError("Email content is required.")
    idle_seconds = parse_idle_seconds(idle_seconds)
    recipients, skipped_domains = filter_gmail_outlook(recipients)
    if not recipients:
        raise AppError(
            "No Gmail or Outlook addresses to send. "
            "Only gmail.com / googlemail.com and Outlook/Hotmail/Live/MSN addresses are sent."
        )

    report = {
        "ok": True,
        "planned": len(recipients),
        "sent": 0,
        "failed": 0,
        "errors": [],
        "idle_seconds": idle_seconds,
        "body_format": body_format,
        "random_style": random_style,
        "styles_used": [],
        "stopped": False,
        "skipped_domains": skipped_domains,
        "resume_email": "",
    }
    if job_path is not None and replace_job:
        start_job(
            job_path,
            recipients=recipients,
            subject=subject,
            body=body,
            body_format=body_format,
            random_style=random_style,
            idle_seconds=idle_seconds,
        )

    def emit(event: dict) -> None:
        if on_progress is not None:
            on_progress(event)
        if log is not None and event.get("message"):
            log(str(event["message"]))

    def stop_requested() -> bool:
        return bool(should_stop and should_stop())

    def finish_stopped(reason: str, *, resume_email: str = "", next_index: int | None = None) -> dict:
        report["stopped"] = True
        report["ok"] = True
        report["error"] = reason
        report["resume_email"] = resume_email
        if job_path is not None:
            index = resume_offset + (next_index if next_index is not None else report["sent"] + report["failed"])
            mark_paused(
                job_path,
                next_index=index,
                resume_email=resume_email,
                sent=report["sent"],
                failed=report["failed"],
                error=reason,
            )
        emit(
            {
                "type": "done",
                **report,
                "done": report["sent"] + report["failed"],
                "message": reason,
                "resumable": True,
            }
        )
        return report

    if random_style:
        format_note = "Random HTML style: each recipient gets a different hiring template."
    else:
        format_note = (
            f"Using {body_format} content."
            if requested_format == body_format
            else f"Content looks like HTML; sending as HTML (was {requested_format})."
        )
    skip_note = f" Skipped {skipped_domains} non-Gmail/Outlook address(es)." if skipped_domains else ""
    emit(
        {
            "type": "start",
            "planned": report["planned"],
            "idle_seconds": idle_seconds,
            "body_format": body_format,
            "random_style": random_style,
            "done": 0,
            "sent": 0,
            "failed": 0,
            "message": f"Starting send to {report['planned']} Gmail/Outlook recipient(s). {format_note}{skip_note}",
        }
    )
    first_email = recipients[0][0] if recipients else ""
    if stop_requested():
        return finish_stopped(
            "Stopped before any email was sent.",
            resume_email=first_email,
            next_index=0,
        )

    mailer = None
    connect_error: TemporaryFailure | None = None
    for attempt, pause in enumerate(TEMPORARY_RETRY_WAITS):
        if stop_requested():
            return finish_stopped(
                "Stopped before any email was sent.",
                resume_email=first_email,
                next_index=0,
            )
        if pause:
            emit(
                {
                    "type": "waiting",
                    "idle_seconds": pause,
                    "done": 0,
                    "sent": 0,
                    "failed": 0,
                    "planned": report["planned"],
                    "message": (
                        f"SMTP connect failed. Retry {attempt}/{len(TEMPORARY_RETRY_WAITS) - 1} "
                        f"in {pause}s."
                    ),
                }
            )
            if wait_idle(pause, sleep, should_stop):
                return finish_stopped(
                    "Stopped while retrying SMTP connect.",
                    resume_email=first_email,
                    next_index=0,
                )
        try:
            mailer = SmtpMailer(settings)
            mailer.connect()
            connect_error = None
            break
        except TemporaryFailure as exc:
            connect_error = exc
            emit(
                {
                    "type": "failed",
                    "error": str(exc),
                    "done": 0,
                    "sent": 0,
                    "failed": 0,
                    "planned": report["planned"],
                    "message": f"Temporary SMTP connect error: {exc}",
                }
            )
            continue
        except AuthFailed:
            raise
        except MailerError as exc:
            raise AppError(str(exc), status=502) from exc
    if mailer is None:
        assert connect_error is not None
        report["ok"] = False
        report["failed"] = 0
        report["error"] = f"Could not open SMTP after retries: {connect_error}"
        report["resume_email"] = first_email
        if job_path is not None:
            mark_paused(
                job_path,
                next_index=resume_offset,
                resume_email=first_email,
                sent=report["sent"],
                failed=report["failed"],
                error=report["error"],
            )
        emit({"type": "done", **report, "message": report["error"], "resumable": True})
        return report

    try:
        for index, (address, name) in enumerate(recipients):
            position = index + 1
            if stop_requested():
                return finish_stopped(
                    f"Stopped. Sent {report['sent']} of {report['planned']}.",
                    resume_email=address,
                    next_index=index,
                )
            if index > 0 and idle_seconds > 0:
                emit(
                    {
                        "type": "waiting",
                        "index": position,
                        "email": address,
                        "idle_seconds": idle_seconds,
                        "done": report["sent"] + report["failed"],
                        "sent": report["sent"],
                        "failed": report["failed"],
                        "planned": report["planned"],
                        "message": f"Waiting {idle_seconds}s before {address}.",
                    }
                )
                if wait_idle(idle_seconds, sleep, should_stop):
                    return finish_stopped(
                        f"Stopped during idle wait. Sent {report['sent']} of {report['planned']}.",
                        resume_email=address,
                        next_index=index,
                    )
            if stop_requested():
                return finish_stopped(
                    f"Stopped. Sent {report['sent']} of {report['planned']}.",
                    resume_email=address,
                    next_index=index,
                )
            style_id = ""
            style_name = ""
            send_body = body
            send_format = body_format
            if random_style:
                picked = pick_random_template(apply_url=settings.apply_url, rng=rng)
                style_id = picked["id"]
                style_name = picked["name"]
                send_body = picked["html"]
                send_format = "html"
                report["styles_used"].append({"email": address, "id": style_id, "name": style_name})
            emit(
                {
                    "type": "sending",
                    "index": position,
                    "email": address,
                    "style_id": style_id,
                    "style_name": style_name,
                    "done": report["sent"] + report["failed"],
                    "sent": report["sent"],
                    "failed": report["failed"],
                    "planned": report["planned"],
                    "message": (
                        f"Sending {position}/{report['planned']} to {address}"
                        + (f" ({style_name})." if style_name else ".")
                    ),
                }
            )
            message = build_compose_message(
                settings,
                to_email=address,
                to_name=name,
                subject=subject,
                body=send_body,
                body_format=send_format,
            )
            try:
                deliver_with_retry(
                    mailer,
                    message,
                    address,
                    sleep=sleep,
                    emit=emit,
                    should_stop=should_stop,
                )
            except RecipientRejected as exc:
                report["failed"] += 1
                report["errors"].append(f"{address}: {exc}")
                if job_path is not None:
                    mark_sent(
                        job_path,
                        index=resume_offset + index + 1,
                        sent=report["sent"],
                        failed=report["failed"],
                    )
                emit(
                    {
                        "type": "failed",
                        "index": position,
                        "email": address,
                        "error": str(exc),
                        "done": report["sent"] + report["failed"],
                        "sent": report["sent"],
                        "failed": report["failed"],
                        "planned": report["planned"],
                        "message": f"Failed {address}: {exc}",
                    }
                )
                continue
            except TemporaryFailure as exc:
                report["failed"] += 1
                report["errors"].append(f"{address}: {exc}")
                report["ok"] = False
                report["error"] = (
                    f"Stopped after temporary mail errors for {address}: {exc}"
                )
                report["resume_email"] = address
                if job_path is not None:
                    mark_paused(
                        job_path,
                        next_index=resume_offset + index,
                        resume_email=address,
                        sent=report["sent"],
                        failed=report["failed"],
                        error=report["error"],
                    )
                emit(
                    {
                        "type": "failed",
                        "index": position,
                        "email": address,
                        "error": str(exc),
                        "done": report["sent"] + report["failed"],
                        "sent": report["sent"],
                        "failed": report["failed"],
                        "planned": report["planned"],
                        "message": report["error"],
                    }
                )
                emit(
                    {
                        "type": "done",
                        **report,
                        "message": report["error"],
                        "resumable": True,
                    }
                )
                return report
            except AuthFailed as exc:
                raise AppError(str(exc), status=401) from exc
            except MailerError as exc:
                if job_path is not None:
                    mark_paused(
                        job_path,
                        next_index=resume_offset + index,
                        resume_email=address,
                        sent=report["sent"],
                        failed=report["failed"],
                        error=str(exc),
                    )
                raise AppError(str(exc), status=502) from exc
            report["sent"] += 1
            if job_path is not None:
                mark_sent(
                    job_path,
                    index=resume_offset + index + 1,
                    sent=report["sent"],
                    failed=report["failed"],
                )
            emit(
                {
                    "type": "sent",
                    "index": position,
                    "email": address,
                    "done": report["sent"] + report["failed"],
                    "sent": report["sent"],
                    "failed": report["failed"],
                    "planned": report["planned"],
                    "message": f"Sent {position}/{report['planned']} to {address}.",
                }
            )
    finally:
        mailer.close()
    if report["failed"] and report["sent"] == 0:
        report["ok"] = False
        report["error"] = report["errors"][0] if report["errors"] else "Send failed."
    if job_path is not None and report["sent"] + report["failed"] >= report["planned"]:
        mark_done(job_path)
    summary = f"Sent {report['sent']} of {report['planned']}."
    if report["failed"]:
        summary += f" Failed {report['failed']}."
    emit({"type": "done", **report, "message": summary})
    return report


def test_login(settings: Settings) -> dict:
    if not valid_email(settings.smtp_username):
        raise AppError(f"{settings.smtp_username} is not a valid email address.")
    try:
        with SmtpMailer(settings):
            return {"ok": True, "message": f"Login ok for {settings.smtp_username}."}
    except AuthFailed as exc:
        raise AppError(str(exc), status=401) from exc
    except MailerError as exc:
        raise AppError(str(exc), status=502) from exc


def make_handler(settings: Settings, web_root: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                path = urlparse(self.path).path
                # Cursor/Chrome DevTools probes this; it is not part of the mail UI.
                if path in {"/json/version", "/json/list"}:
                    self._send(404, "", "text/plain; charset=utf-8")
                    return
                if path in {"/", "/index.html"}:
                    self._file(web_root / "index.html", "text/html; charset=utf-8")
                    return
                if path == "/health":
                    self._json(200, {"ok": True})
                    return
                if path == "/api/logs":
                    self._json(200, {"ok": True, "lines": recent_logs()})
                    return
                if path == "/api/resume":
                    job = resumable_job(job_path_for(settings.database_path))
                    if job is None:
                        self._json(200, {"ok": True, "resumable": False})
                        return
                    self._json(200, {**job, "resumable": True})
                    return
                if path == "/api/defaults":
                    self._json(
                        200,
                        {
                            "email": settings.smtp_username,
                            "from_name": settings.from_name,
                            "physical_address": settings.physical_address,
                            "password_saved": bool(settings.smtp_password),
                            "smtp_host": settings.smtp_host,
                            "idle_seconds": default_idle_seconds(settings),
                            "hourly_limit": settings.hourly_limit,
                        },
                    )
                    return
                if path == "/api/templates":
                    write_template_files()
                    self._json(200, {"ok": True, "templates": list_templates()})
                    return
                if path.startswith("/api/templates/"):
                    template_id = unquote(path[len("/api/templates/") :]).strip("/")
                    if not template_id or "/" in template_id or ".." in template_id:
                        self._json(404, {"ok": False, "error": "Template not found."})
                        return
                    template = get_template(template_id)
                    if template is None:
                        self._json(404, {"ok": False, "error": f"Template not found: {template_id}"})
                        return
                    self._json(200, {"ok": True, **template})
                    return
                if path.startswith("/static/"):
                    self._static(path[len("/static/") :])
                    return
                token = _token(path)
                if token is not None:
                    self._unsubscribe_get(token)
                    return
                self._json(404, {"ok": False, "error": "Not found."})
            except Exception as exc:  # noqa: BLE001
                self._handle_exception(exc)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            token = _token(path)
            if token is not None:
                try:
                    self._unsubscribe_post(token)
                except Exception as exc:  # noqa: BLE001
                    self._handle_exception(exc)
                return
            streaming = False
            try:
                payload = self._read_json()
                if path == "/api/test-login":
                    append_log(f"POST /api/test-login for {payload.get('email', '')}")
                    live = settings_with_overrides(
                        settings,
                        email=str(payload.get("email", "")),
                        password=str(payload.get("password", "")),
                        from_name=str(payload.get("from_name", "")),
                    )
                    result = test_login(live)
                    append_log(result.get("message", "Login ok."))
                    self._json(200, {**result, "logs": recent_logs(40)})
                    return
                if path == "/api/parse-list":
                    filename = str(payload.get("filename", "upload.csv"))
                    content = str(payload.get("content", ""))
                    append_log(f"POST /api/parse-list ({filename})")
                    result = parse_recipient_upload(filename, content)
                    append_log(result["message"])
                    self._json(200, {**result, "logs": recent_logs(40)})
                    return
                if path == "/api/send":
                    live = settings_with_overrides(
                        settings,
                        email=str(payload.get("email", "")),
                        password=str(payload.get("password", "")),
                        from_name=str(payload.get("from_name", "")),
                        physical_address=str(payload.get("physical_address", "")),
                    )
                    job_file = job_path_for(settings.database_path)
                    resume = parse_bool(payload.get("resume", False))
                    resume_offset = 0
                    if resume:
                        saved = resumable_job(job_file)
                        disk = load_job(job_file)
                        if saved is None or disk is None:
                            raise AppError("No paused send to resume.")
                        recipients = recipients_from_job(disk)
                        if not recipients:
                            clear_job(job_file)
                            raise AppError("Paused send has no remaining recipients.")
                        resume_offset = int(disk.get("next_index", 0) or 0)
                        subject = str(disk.get("subject") or payload.get("subject", ""))
                        body = str(disk.get("body") or payload.get("body", ""))
                        body_format = parse_body_format(
                            disk.get("body_format") or payload.get("body_format", "plain")
                        )
                        random_style = parse_bool(
                            disk.get("random_style", payload.get("random_style", False))
                        )
                        idle = parse_idle_seconds(
                            disk.get("idle_seconds", payload.get("idle_seconds")),
                            fallback=default_idle_seconds(live),
                        )
                        append_log(
                            f"POST /api/send resume from {saved.get('resume_email', '')} "
                            f"({len(recipients)} remaining)"
                        )
                    else:
                        recipients = parse_recipients(str(payload.get("to", "")))
                        idle = parse_idle_seconds(
                            payload.get("idle_seconds"),
                            fallback=default_idle_seconds(live),
                        )
                        body_format = parse_body_format(payload.get("body_format", "plain"))
                        random_style = parse_bool(payload.get("random_style", False))
                        subject = str(payload.get("subject", ""))
                        body = str(payload.get("body", ""))
                        append_log(
                            f"POST /api/send to {len(recipients)} recipient(s), "
                            f"format={body_format}, random_style={random_style}, idle={idle}s"
                        )
                    streaming = True
                    self._stream_send(
                        live,
                        recipients=recipients,
                        subject=subject,
                        body=body,
                        body_format=body_format,
                        random_style=random_style,
                        idle_seconds=idle,
                        job_path=job_file,
                        resume_offset=resume_offset if resume else 0,
                        fresh_job=not resume,
                    )
                    return
                if path == "/api/clear-resume":
                    clear_job(job_path_for(settings.database_path))
                    append_log("Cleared paused send job.")
                    self._json(
                        200,
                        {
                            "ok": True,
                            "message": "Resume state cleared.",
                            "logs": recent_logs(40),
                        },
                    )
                    return
                self._json(404, {"ok": False, "error": "Not found.", "logs": recent_logs(40)})
            except AppError as exc:
                append_log(f"ERROR {path}: {exc}")
                if streaming:
                    return
                self._json(
                    exc.status,
                    {"ok": False, "error": str(exc), "logs": recent_logs(40)},
                )
            except ConfigError as exc:
                append_log(f"ERROR {path}: {exc}")
                if streaming:
                    return
                self._json(400, {"ok": False, "error": str(exc), "logs": recent_logs(40)})
            except json.JSONDecodeError as exc:
                append_log(f"ERROR {path}: invalid JSON ({exc})")
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "Request body must be JSON.",
                        "detail": str(exc),
                        "logs": recent_logs(40),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                if streaming:
                    append_log(f"ERROR {path}: {exc}")
                    return
                self._handle_exception(exc)

        def _stream_send(
            self,
            live: Settings,
            *,
            recipients: list[tuple[str, str]],
            subject: str,
            body: str,
            body_format: str,
            random_style: bool = False,
            idle_seconds: int,
            job_path: Path | None = None,
            resume_offset: int = 0,
            fresh_job: bool = True,
        ) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()

            stopped = threading.Event()

            def should_stop() -> bool:
                if stopped.is_set():
                    return True
                if peer_closed(self.connection):
                    stopped.set()
                    return True
                return False

            def write_event(event: dict) -> None:
                if stopped.is_set() and event.get("type") not in {"done", "error"}:
                    return
                if event.get("message"):
                    append_log(str(event["message"]))
                if event.get("type") == "error" and event.get("error"):
                    append_log(f"ERROR {event['error']}")
                if event.get("stopped"):
                    append_log("Send stopped by client.")
                payload = dict(event)
                if event.get("type") in {"done", "error"}:
                    payload["logs"] = recent_logs(60)
                line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    stopped.set()

            try:
                send_compose(
                    live,
                    recipients=recipients,
                    subject=subject,
                    body=body,
                    body_format=body_format,
                    random_style=random_style,
                    idle_seconds=idle_seconds,
                    on_progress=write_event,
                    should_stop=should_stop,
                    job_path=job_path,
                    resume_offset=resume_offset if not fresh_job else 0,
                    replace_job=fresh_job,
                )
            except AppError as exc:
                append_log(f"ERROR send: {exc}")
                if job_path is not None and recipients:
                    mark_paused(
                        job_path,
                        next_index=resume_offset,
                        resume_email=recipients[0][0],
                        sent=0,
                        failed=0,
                        error=str(exc),
                    )
                write_event(
                    {
                        "type": "error",
                        "ok": False,
                        "error": str(exc),
                        "message": str(exc),
                        "resumable": True,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - surface unexpected SMTP failures to the UI
                detail = f"{exc}\n{traceback.format_exc()}"
                append_log(f"ERROR send: {exc}")
                if job_path is not None and recipients:
                    mark_paused(
                        job_path,
                        next_index=resume_offset,
                        resume_email=recipients[0][0],
                        sent=0,
                        failed=0,
                        error=str(exc),
                    )
                write_event(
                    {
                        "type": "error",
                        "ok": False,
                        "error": str(exc),
                        "detail": detail,
                        "message": str(exc),
                        "resumable": True,
                    }
                )

        def _handle_exception(self, exc: Exception) -> None:
            detail = traceback.format_exc()
            append_log(f"ERROR {exc}")
            append_log(detail.strip().splitlines()[-1] if detail.strip() else str(exc))
            try:
                self._json(
                    500,
                    {
                        "ok": False,
                        "error": str(exc) or exc.__class__.__name__,
                        "detail": detail,
                        "logs": recent_logs(60),
                    },
                )
            except Exception:  # noqa: BLE001
                pass

        def log_message(self, fmt: str, *args) -> None:
            message = fmt % args
            # Skip browser noise: DevTools probes and repeated page/asset loads.
            if any(
                marker in message
                for marker in (
                    "/json/version",
                    "/json/list",
                    '"GET / HTTP/',
                    '"GET /index.html HTTP/',
                    '"GET /static/',
                    '"GET /favicon.ico HTTP/',
                    '"GET /health HTTP/',
                    '"GET /api/logs HTTP/',
                    '"GET /api/defaults HTTP/',
                    '"GET /api/templates HTTP/',
                )
            ):
                return
            message = re.sub(r"/u/[A-Za-z0-9_-]+", "/u/[redacted]", message)
            append_log(f"{self.address_string()} {message}")

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError as exc:
                raise AppError("Invalid Content-Length.") from exc
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise AppError("Request is too large.")
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise AppError("JSON body must be an object.")
            return data

        def _static(self, relative: str) -> None:
            relative = unquote(relative).replace("\\", "/")
            if not relative or ".." in relative.split("/"):
                self._send(404, "Not found.", "text/plain; charset=utf-8")
                return
            path = (web_root / "static" / relative).resolve()
            root = (web_root / "static").resolve()
            if root not in path.parents and path != root:
                self._send(404, "Not found.", "text/plain; charset=utf-8")
                return
            if not path.is_file():
                self._send(404, "Not found.", "text/plain; charset=utf-8")
                return
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {
                "application/javascript",
                "application/json",
            }:
                content_type = f"{content_type}; charset=utf-8"
            self._bytes(200, path.read_bytes(), content_type)

        def _file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                self._send(404, "Not found.", "text/plain; charset=utf-8")
                return
            self._bytes(200, path.read_bytes(), content_type)

        def _unsubscribe_get(self, token: str) -> None:
            person = _lookup(settings.database_path, token)
            if person is None:
                self._send(404, "This unsubscribe link is not valid.", "text/plain; charset=utf-8")
                return
            already = person.status == "unsubscribed"
            self._send(
                200,
                _unsubscribe_page(person.email, settings.from_name, token, already=already, done=False),
                "text/html; charset=utf-8",
            )

        def _unsubscribe_post(self, token: str) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            if length > 0:
                self.rfile.read(min(length, 1_000_000))
            with Store(settings.database_path) as store:
                person = store.unsubscribe(token=token)
            if person is None:
                self._send(404, "This unsubscribe link is not valid.", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                _unsubscribe_page(person.email, settings.from_name, token, already=False, done=True),
                "text/html; charset=utf-8",
            )

        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self._bytes(status, data, "application/json; charset=utf-8")

        def _send(self, status: int, body: str, content_type: str) -> None:
            self._bytes(status, body.encode("utf-8"), content_type)

        def _bytes(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8080, root: Path | None = None) -> None:
    root = root or project_root()
    settings = load_settings(root)
    with Store(settings.database_path):
        pass
    web_root = root / "web"
    if not (web_root / "index.html").is_file():
        raise ConfigError(f"Missing web UI at {web_root / 'index.html'}")
    written = write_template_files(root)
    html_count = sum(1 for path in written if path.suffix == ".html")
    text_count = sum(1 for path in written if path.suffix == ".txt")
    append_log(f"Hiring templates ready: {html_count} HTML + {text_count} plain text.")
    server = ThreadingHTTPServer((host, port), make_handler(settings, web_root))
    append_log(f"HireLens Mail at http://{host}:{port}")
    append_log("Open that URL, enter email, password, and content, then send.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def _lookup(database: Path, token: str):
    with Store(database) as store:
        return store.get_by_token(token)


def _token(path: str) -> str | None:
    prefix = "/u/"
    if not path.startswith(prefix):
        return None
    token = path[len(prefix) :]
    if TOKEN_RE.fullmatch(token) is None:
        return None
    return token


def _unsubscribe_page(email: str, from_name: str, token: str, *, already: bool, done: bool) -> str:
    safe_email = escape(email)
    safe_name = escape(from_name)
    safe_token = escape(token)
    if done or already:
        lead = f"{safe_email} is unsubscribed from {safe_name} and will not get another campaign."
        button = ""
    else:
        lead = f"Remove {safe_email} from {safe_name} emails?"
        button = (
            f'<form method="post" action="/u/{safe_token}">'
            '<button type="submit">Unsubscribe</button>'
            "</form>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Unsubscribe</title>
</head>
<body style="font-family:Georgia,serif;margin:40px;color:#102a36;">
  <h1>Unsubscribe</h1>
  <p>{lead}</p>
  {button}
</body>
</html>
"""
