"""Command line for the Private Email campaign sender."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from mailbot.campaign import CampaignError, load_template, prepare_campaign, send_campaign
from mailbot.config import ConfigError, load_settings, project_root
from mailbot.dnscheck import check_domain, format_findings
from mailbot.message import build_message
from mailbot.render import TemplateError
from mailbot.smtp_client import AuthFailed, MailerError, SmtpMailer
from mailbot.store import Store, Subscriber
from mailbot.webapp import serve


EXAMPLES = """
examples:
  python -m mailbot init
  python -m mailbot doctor
  python -m mailbot contacts add --email ada@example.com --name "Ada Lovelace" --source "website form"
  python -m mailbot contacts import contacts.csv --source "customer export"
  python -m mailbot campaign preview --subject "Hi {{first_name}}" --html templates/intro.html --text templates/intro.txt
  python -m mailbot campaign send --name intro --subject "Hi {{first_name}}" --html templates/intro.html --text templates/intro.txt --dry-run
  python -m mailbot serve
  # Then open http://127.0.0.1:8080 and enter email, password, and content.
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, CampaignError, TemplateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except AuthFailed as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Messages the server already accepted are saved. Run the same send again to continue.")
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailbot",
        description="Send opt-in campaigns from a Namecheap Private Email mailbox.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create .env and the subscriber database")
    init.set_defaults(func=cmd_init)

    doctor = commands.add_parser("doctor", help="Check DNS records and SMTP login")
    doctor.set_defaults(func=cmd_doctor)

    contacts = commands.add_parser("contacts", help="Manage the opt-in list")
    contact_commands = contacts.add_subparsers(dest="contacts_command", required=True)

    add = contact_commands.add_parser("add", help="Add one person who opted in")
    add.add_argument("--email", required=True)
    add.add_argument("--name", default="")
    add.add_argument("--company", default="")
    add.add_argument("--source", required=True, help="Where this person opted in")
    add.set_defaults(func=cmd_contacts_add)

    imported = contact_commands.add_parser("import", help="Import a CSV of people who opted in")
    imported.add_argument("path")
    imported.add_argument("--source", required=True, help="Where these people opted in")
    imported.set_defaults(func=cmd_contacts_import)

    listing = contact_commands.add_parser("list", help="Show the list")
    listing.add_argument("--status", choices=["subscribed", "unsubscribed"])
    listing.set_defaults(func=cmd_contacts_list)

    unsubscribe = contact_commands.add_parser("unsubscribe", help="Stop mail to one address")
    unsubscribe.add_argument("--email", required=True)
    unsubscribe.set_defaults(func=cmd_contacts_unsubscribe)

    remove = contact_commands.add_parser("remove", help="Delete an address that has never been mailed")
    remove.add_argument("--email", required=True)
    remove.set_defaults(func=cmd_contacts_remove)

    campaign = commands.add_parser("campaign", help="Preview or send a campaign")
    campaign_commands = campaign.add_subparsers(dest="campaign_command", required=True)

    preview = campaign_commands.add_parser("preview", help="Render one message without sending it")
    _add_template_args(preview)
    preview.add_argument("--email", help="Subscriber to preview. Defaults to the first subscribed address.")
    preview.set_defaults(func=cmd_preview)

    send = campaign_commands.add_parser("send", help="Send to subscribed people who have not received this campaign")
    send.add_argument("--name", required=True, help="Campaign id. Re-running the same name skips people already sent.")
    _add_template_args(send)
    send.add_argument("--dry-run", action="store_true", help="Print recipients and subjects without sending")
    send.add_argument("--yes", action="store_true", help="Required for a live send")
    send.add_argument("--limit", type=int, help="Send at most this many messages in this run")
    send.set_defaults(func=cmd_send)

    status = campaign_commands.add_parser("status", help="Show send counts for a campaign")
    status.add_argument("--name", required=True)
    status.set_defaults(func=cmd_status)

    server = commands.add_parser("serve", help="Open the HireLens Mail web app")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    server.set_defaults(func=cmd_serve)
    return parser


def _add_template_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--subject", required=True, help="Subject. Supports {{first_name}} and other fields.")
    parser.add_argument("--html", required=True, help="HTML template path")
    parser.add_argument("--text", required=True, help="Plain-text template path")


def cmd_init(_args: argparse.Namespace) -> int:
    root = project_root()
    env_path = root / ".env"
    example = root / ".env.example"
    if env_path.exists():
        print(f"{env_path} already exists.")
    elif example.is_file():
        shutil.copyfile(example, env_path)
        print(f"Created {env_path}. Set SMTP_PASSWORD and PHYSICAL_ADDRESS.")
    else:
        print(f"Missing {example}.", file=sys.stderr)
        return 1
    settings = load_settings(root)
    with Store(settings.database_path):
        pass
    print(f"Subscriber database: {settings.database_path}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    settings = load_settings()
    domain = settings.from_email.split("@", 1)[1]
    print(f"From: {settings.from_name} <{settings.from_email}>")
    print(f"SMTP: {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security})")
    print(f"Password: {'set' if settings.smtp_password else 'missing'}")
    print(f"Hourly limit: {settings.hourly_limit}")
    if settings.hourly_limit <= 20:
        print("This stays under the 20/hour trial cap. A paid mailbox can use HOURLY_LIMIT=400.")
    print(f"Physical address: {settings.physical_address or 'missing'}")
    print(f"Unsubscribe links: {settings.public_base_url or 'reply-to until PUBLIC_BASE_URL is set'}")
    print(f"DNS for {domain}:")
    findings = check_domain(domain)
    print(format_findings(findings))
    smtp_failed = False
    if not settings.smtp_password:
        print("Skipped SMTP login because SMTP_PASSWORD is empty.")
    else:
        print("Checking SMTP login...")
        try:
            with SmtpMailer(settings):
                print("SMTP login ok.")
        except MailerError as exc:
            smtp_failed = True
            print(str(exc), file=sys.stderr)
    blocked = any(item.blocking for item in findings)
    return 1 if blocked or smtp_failed else 0


def cmd_contacts_add(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        with Store(settings.database_path) as store:
            row, outcome = store.add_subscriber(
                args.email,
                name=args.name,
                company=args.company,
                consent_source=args.source,
            )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if outcome == "invalid" or row is None:
        print(f"{args.email} is not an email address.", file=sys.stderr)
        return 1
    if outcome == "duplicate":
        print(f"{row.email} is already on the list.")
        return 0
    if outcome == "suppressed":
        print(f"{row.email} is unsubscribed and was not added back.")
        return 0
    print(f"Added {row.email}.")
    return 0


def cmd_contacts_import(args: argparse.Namespace) -> int:
    settings = load_settings()
    path = Path(args.path)
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    try:
        with Store(settings.database_path) as store:
            result = store.import_csv(path, consent_source=args.source)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Added {result.added}. Already listed {result.duplicates}. "
        f"Left unsubscribed {result.suppressed}. Invalid {result.invalid}."
    )
    for line in result.invalid_rows[:20]:
        print(line, file=sys.stderr)
    return 1 if result.invalid else 0


def cmd_contacts_list(args: argparse.Namespace) -> int:
    settings = load_settings()
    with Store(settings.database_path) as store:
        rows = store.list_subscribers(args.status)
    if not rows:
        print("No matching subscribers.")
        return 0
    for row in rows:
        print(f"{row.email}\t{row.name or '-'}\t{row.status}\t{row.consent_source}")
    print(f"{len(rows)} total")
    return 0


def cmd_contacts_unsubscribe(args: argparse.Namespace) -> int:
    settings = load_settings()
    with Store(settings.database_path) as store:
        person = store.unsubscribe(email=args.email)
    if person is None:
        print(f"No subscriber with the address {args.email}.", file=sys.stderr)
        return 1
    print(f"{person.email} is unsubscribed.")
    return 0


def cmd_contacts_remove(args: argparse.Namespace) -> int:
    settings = load_settings()
    with Store(settings.database_path) as store:
        outcome = store.remove_subscriber(args.email)
    if outcome == "missing":
        print(f"No subscriber with the address {args.email}.", file=sys.stderr)
        return 1
    if outcome == "unsubscribed":
        print(f"{args.email} already received mail, so it was unsubscribed instead of deleted.")
        return 0
    print(f"Removed {args.email}.")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    settings = load_settings()
    html_template = load_template(Path(args.html))
    text_template = load_template(Path(args.text))
    with Store(settings.database_path) as store:
        if args.email:
            person = store.get_by_email(args.email)
            if person is None:
                print(f"{args.email} is not on the list.", file=sys.stderr)
                return 1
        else:
            subscribed = store.list_subscribers("subscribed")
            person = subscribed[0] if subscribed else _sample()
    message = build_message(
        settings,
        person,
        subject_template=args.subject,
        html_template=html_template,
        text_template=text_template,
    )
    print(f"To: {message['To']}")
    print(f"From: {message['From']}")
    print(f"Subject: {message['Subject']}")
    print()
    plain = message.get_body(preferencelist=("plain",))
    print(plain.get_content() if plain is not None else "")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not args.dry_run and args.yes:
        settings.require_live_send()
    html_template = load_template(Path(args.html))
    text_template = load_template(Path(args.text))
    with Store(settings.database_path) as store:
        campaign_id, recipients = prepare_campaign(
            store,
            settings,
            name=args.name,
            subject=args.subject,
            html_template=html_template,
            text_template=text_template,
            limit=args.limit,
        )
        if not recipients:
            if store.list_subscribers("subscribed"):
                print(f"Everyone still subscribed already received {args.name}.")
            else:
                print("The list has no subscribed addresses. Add people who opted in before sending.")
            return 0
        spread = 3600 / settings.hourly_limit
        print(
            f"{args.name}: {len(recipients)} message(s) from {settings.from_name} <{settings.from_email}>."
        )
        print(f"Pace: {settings.hourly_limit} per hour, about {spread:.0f} seconds apart.")
        if settings.hourly_limit <= 20:
            print("This stays under the 20/hour trial cap. A paid mailbox can use HOURLY_LIMIT=400.")
        if not settings.physical_address:
            print("PHYSICAL_ADDRESS is empty. A live send will stop until you set it in .env.")
        if args.dry_run:
            for person in recipients:
                message = build_message(
                    settings,
                    person,
                    subject_template=args.subject,
                    html_template=html_template,
                    text_template=text_template,
                )
                print(f"dry-run {person.email} | {message['Subject']}")
            print("Dry run only. Nothing was sent.")
            return 0
        if not args.yes:
            print("Nothing sent. Re-run the same command with --yes to send.")
            return 2
        with SmtpMailer(settings) as mailer:
            report = send_campaign(
                store,
                settings,
                mailer,
                campaign_id=campaign_id,
                recipients=recipients,
                subject=args.subject,
                html_template=html_template,
                text_template=text_template,
            )
    print(f"Sent {report.sent}. Failed {report.failed}.")
    if report.stopped or report.failed:
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings()
    with Store(settings.database_path) as store:
        campaign_id = store.campaign_id(args.name)
        if campaign_id is None:
            print(f"No campaign named {args.name}.", file=sys.stderr)
            return 1
        counts = store.campaign_counts(campaign_id)
        remaining = len(store.pending_recipients(campaign_id))
    print(f"sent {counts['sent']}")
    print(f"failed attempts {counts['failed']}")
    print(f"remaining {remaining}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    serve(host=args.host, port=args.port)
    return 0


def _sample() -> Subscriber:
    return Subscriber(
        id=0,
        email="subscriber@example.com",
        name="Alex Example",
        company="Example Co",
        status="subscribed",
        consent_source="preview",
        unsubscribe_token="preview-token-not-saved",
    )
