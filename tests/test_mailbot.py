"""Tests that never open a network connection."""

from __future__ import annotations

import json
import random
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mailbot.campaign import prepare_campaign, send_campaign
from mailbot.cli import main
from mailbot.config import ConfigError, load_settings, settings_with_overrides
from mailbot.dnscheck import interpret
from mailbot.message import (
    build_compose_message,
    build_message,
    content_to_html,
    html_to_text,
    resolve_body_format,
)
from mailbot.pace import next_delay
from mailbot.server import make_handler
from mailbot.smtp_client import AuthFailed, RecipientRejected, TemporaryFailure, _raise_for_code
from mailbot.store import Store
from mailbot.hiring_templates import (
    SUBJECT,
    get_template,
    list_templates,
    pick_random_template,
    write_template_files,
)
from mailbot.webapp import (
    AppError,
    parse_idle_seconds,
    parse_recipient_upload,
    parse_recipients,
    send_compose,
    wait_idle,
)
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = [
    "MAILBOT_ROOT",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "FROM_NAME",
    "FROM_EMAIL",
    "REPLY_TO",
    "HOURLY_LIMIT",
    "PHYSICAL_ADDRESS",
    "PUBLIC_BASE_URL",
    "APPLY_URL",
    "DATABASE_PATH",
]


class EnvMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: __import__("os").environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            __import__("os").environ.pop(key, None)

    def tearDown(self) -> None:
        import os

        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def settings(root: Path, **overrides):
    values = {
        "smtp_host": "mail.privateemail.com",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_username": "davidyang@hirelens.us",
        "smtp_password": "secret",
        "from_name": "HireLens",
        "from_email": "davidyang@hirelens.us",
        "reply_to": "davidyang@hirelens.us",
        "hourly_limit": 500,
        "physical_address": "1 Market Street, San Francisco, CA 94105",
        "public_base_url": "https://example.com",
        "apply_url": "https://forms.gle/d5ifR5Htk7jwRxmC9",
        "database_path": root / "mailbot.db",
        "env_path": root / ".env",
    }
    values.update(overrides)
    from mailbot.config import Settings

    return Settings(**values)


class SmtpCodeTests(unittest.TestCase):
    def test_classifies_permanent_temporary_and_auth_failures(self) -> None:
        with self.assertRaises(RecipientRejected):
            _raise_for_code(550, "no such user")
        with self.assertRaises(TemporaryFailure):
            _raise_for_code(451, "try later")
        with self.assertRaises(AuthFailed):
            _raise_for_code(535, "authentication failed")


class PaceTests(unittest.TestCase):
    def test_spreads_messages_across_the_hour(self) -> None:
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        sent = [now - timedelta(seconds=10)]
        self.assertAlmostEqual(next_delay(sent, now, 15), 230)

    def test_waits_until_the_hourly_window_opens(self) -> None:
        now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        sent = [now - timedelta(minutes=30), now - timedelta(seconds=5)]
        self.assertAlmostEqual(next_delay(sent, now, 2), 30 * 60 + 1)

    def test_sends_immediately_when_idle(self) -> None:
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(next_delay([], now, 15), 0)


class ConfigTests(EnvMixin):
    def test_password_is_hidden_and_paid_limit_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text(
                "HOURLY_LIMIT=400\nSMTP_PASSWORD=super-secret\n",
                encoding="utf-8",
            )
            loaded = load_settings(root)
        self.assertEqual(loaded.hourly_limit, 400)
        self.assertEqual(loaded.from_email, "davidyang@hirelens.us")
        self.assertNotIn("super-secret", repr(loaded))

    def test_limit_above_private_email_cap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text("HOURLY_LIMIT=501\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_settings(root)


class ListTests(unittest.TestCase):
    def test_import_dedupes_and_does_not_resubscribe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path = root / "people.csv"
            csv_path.write_text(
                "email,name,company,role\n"
                "Ada@Example.com,Ada Lovelace,Engines,Analyst\n"
                "not-an-email,Nope,Co,\n"
                "ada@example.com,Ada L,Engines,Analyst\n",
                encoding="utf-8",
            )
            with Store(root / "mail.db") as store:
                result = store.import_csv(csv_path, consent_source="website form")
                self.assertEqual(result.added, 1)
                self.assertEqual(result.duplicates, 1)
                self.assertEqual(result.invalid, 1)
                person = store.get_by_email("ada@example.com")
                assert person is not None
                self.assertEqual(person.fields["role"], "Analyst")
                self.assertEqual(person.name, "Ada L")
                store.unsubscribe(email=person.email)
                again = store.import_csv(csv_path, consent_source="website form")
                self.assertEqual(again.suppressed, 2)
                self.assertEqual(again.added, 0)
                self.assertEqual(store.get_by_email("ada@example.com").status, "unsubscribed")

    def test_accepts_email_header_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            csv_path = root / "people.csv"
            csv_path.write_text(
                "E-mail,Full Name\nada@example.com,Ada Lovelace\n",
                encoding="utf-8",
            )
            with Store(root / "mail.db") as store:
                result = store.import_csv(csv_path, consent_source="export")
                person = store.get_by_email("ada@example.com")
            self.assertEqual(result.added, 1)
            assert person is not None
            self.assertEqual(person.name, "Ada Lovelace")


class MessageTests(unittest.TestCase):
    def test_escapes_html_and_blocks_header_injection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder))
            with Store(Path(folder) / "mail.db") as store:
                person, outcome = store.add_subscriber(
                    "ada@example.com",
                    name="Ada\nBcc: evil@example.com",
                    consent_source="form",
                )
            assert person is not None and outcome == "added"
            person.name = "<b>Ada</b>\nBcc: evil@example.com"
            message = build_message(
                loaded,
                person,
                subject_template="Hello\nBcc: evil@example.com {{first_name}}",
                html_template="<p>Hi {{first_name}}</p>",
                text_template="Hi {{first_name}}",
            )
            self.assertNotIn("\n", message["Subject"])
            self.assertNotIn("\r", message["Subject"])
            self.assertIsNone(message["Bcc"])
            self.assertNotIn("\n", message["To"])
            html = message.get_body(preferencelist=("html",)).get_content()
            self.assertIn("&lt;b&gt;Ada&lt;/b&gt;", html)
            self.assertNotIn("<b>Ada</b>", html)
            text = message.get_body(preferencelist=("plain",)).get_content()
            self.assertIn(loaded.physical_address, text)
            self.assertIn("Unsubscribe:", text)
            self.assertIn(person.unsubscribe_token, message["List-Unsubscribe"])
            self.assertIn("List-Unsubscribe-Post", message)


class CampaignTests(unittest.TestCase):
    def test_retries_failures_and_skips_people_already_sent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            loaded = settings(root)
            with Store(root / "mail.db") as store:
                store.add_subscriber("a@example.com", name="Ada", consent_source="form")
                store.add_subscriber("b@example.com", name="Grace", consent_source="form")
                campaign_id, recipients = prepare_campaign(
                    store,
                    loaded,
                    name="intro",
                    subject="Hi {{first_name}}",
                    html_template="<p>Hi {{first_name}}</p>",
                    text_template="Hi {{first_name}}",
                    limit=None,
                )
                self.assertEqual([person.email for person in recipients], ["a@example.com", "b@example.com"])

                class RejectFirst:
                    def __init__(self) -> None:
                        self.sent: list[str] = []

                    def send(self, message, recipient: str) -> None:
                        if recipient == "a@example.com":
                            raise RecipientRejected("550 user unknown")
                        self.sent.append(recipient)

                report = send_campaign(
                    store,
                    loaded,
                    RejectFirst(),
                    campaign_id=campaign_id,
                    recipients=recipients,
                    subject="Hi {{first_name}}",
                    html_template="<p>Hi {{first_name}}</p>",
                    text_template="Hi {{first_name}}",
                    log=lambda _message: None,
                    sleep=lambda _seconds: None,
                )
                self.assertEqual(report.sent, 1)
                self.assertEqual(report.failed, 1)

                class Accept:
                    def __init__(self) -> None:
                        self.sent: list[str] = []

                    def send(self, message, recipient: str) -> None:
                        self.sent.append(recipient)

                mailer = Accept()
                _campaign_id, remaining = prepare_campaign(
                    store,
                    loaded,
                    name="intro",
                    subject="Hi {{first_name}}",
                    html_template="<p>Hi {{first_name}}</p>",
                    text_template="Hi {{first_name}}",
                    limit=None,
                )
                self.assertEqual([person.email for person in remaining], ["a@example.com"])
                retried = send_campaign(
                    store,
                    loaded,
                    mailer,
                    campaign_id=campaign_id,
                    recipients=remaining,
                    subject="Hi {{first_name}}",
                    html_template="<p>Hi {{first_name}}</p>",
                    text_template="Hi {{first_name}}",
                    log=lambda _message: None,
                    sleep=lambda _seconds: None,
                )
                self.assertEqual(retried.sent, 1)
                self.assertEqual(mailer.sent, ["a@example.com"])


class MissingPlaceholderTests(unittest.TestCase):
    def test_prepare_stops_before_any_send(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            loaded = settings(root)
            with Store(root / "mail.db") as store:
                store.add_subscriber("a@example.com", name="Ada", consent_source="form")
                from mailbot.campaign import CampaignError

                with self.assertRaises(CampaignError):
                    prepare_campaign(
                        store,
                        loaded,
                        name="intro",
                        subject="Hi {{not_a_field}}",
                        html_template="<p>Hi</p>",
                        text_template="Hi",
                        limit=None,
                    )


class ServerTests(unittest.TestCase):
    def test_get_does_not_unsubscribe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "mail.db"
            with Store(database) as store:
                person, _outcome = store.add_subscriber(
                    "ada@example.com",
                    name="Ada",
                    consent_source="form",
                )
            assert person is not None
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(database, "HireLens"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            url = f"http://127.0.0.1:{port}/u/{person.unsubscribe_token}"
            try:
                page = _get(url)
                self.assertIn("Unsubscribe", page)
                with Store(database) as store:
                    self.assertEqual(store.get_by_token(person.unsubscribe_token).status, "subscribed")
                request = urllib.request.Request(url, data=b"", method="POST")
                with urllib.request.urlopen(request, timeout=2) as response:
                    done = response.read().decode()
                self.assertIn("unsubscribed", done.lower())
                with Store(database) as store:
                    self.assertEqual(store.get_by_token(person.unsubscribe_token).status, "unsubscribed")
            finally:
                server.shutdown()
                server.server_close()


class DnsInterpretationTests(unittest.TestCase):
    def test_private_email_records(self) -> None:
        findings = interpret(
            "mx1.privateemail.com mx2.privateemail.com",
            "v=spf1 include:spf.privateemail.com ~all",
            "v=DKIM1; k=rsa; p=abc",
            "",
            "",
        )
        by_label = {item.label: item.status for item in findings}
        self.assertEqual(by_label["MX"], "ok")
        self.assertEqual(by_label["SPF"], "ok")
        self.assertEqual(by_label["DKIM"], "ok")
        self.assertEqual(by_label["DMARC"], "warn")


class CliTests(EnvMixin):
    def test_dry_run_sends_nothing(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            os.environ["MAILBOT_ROOT"] = str(root)
            (root / ".env").write_text(
                "SMTP_PASSWORD=\nPHYSICAL_ADDRESS=1 Main St\nHOURLY_LIMIT=15\n",
                encoding="utf-8",
            )
            (root / ".env.example.privateemail").write_text(
                "SMTP_PASSWORD=\nPHYSICAL_ADDRESS=1 Main St\nHOURLY_LIMIT=15\n",
                encoding="utf-8",
            )
            csv_path = root / "people.csv"
            csv_path.write_text("email,name\nada@example.com,Ada Lovelace\n", encoding="utf-8")
            html_path = root / "note.html"
            text_path = root / "note.txt"
            html_path.write_text("<p>Hi {{first_name}}</p>", encoding="utf-8")
            text_path.write_text("Hi {{first_name}}\n", encoding="utf-8")
            self.assertEqual(
                main(["contacts", "import", str(csv_path), "--source", "website form"]),
                0,
            )
            send_args = [
                "campaign",
                "send",
                "--name",
                "intro",
                "--subject",
                "Hi {{first_name}}",
                "--html",
                str(html_path),
                "--text",
                str(text_path),
            ]
            self.assertEqual(main(send_args), 2)
            self.assertEqual(main([*send_args, "--yes"]), 1)
            self.assertEqual(main([*send_args, "--dry-run"]), 0)
            with Store(root / "data" / "mailbot.db") as store:
                self.assertEqual(store.campaign_counts(store.campaign_id("intro")).get("sent", 0), 0)


class WebComposeTests(EnvMixin):
    def test_parse_recipients_and_compose_html(self) -> None:
        people = parse_recipients("Ada Lovelace <ada@example.com>\ngrace@example.com")
        self.assertEqual(
            people,
            [("ada@example.com", "Ada Lovelace"), ("grace@example.com", "")],
        )
        html = content_to_html("Hello\n\nLine two")
        self.assertIn("Hello", html)
        self.assertIn("Line two", html)
        with self.assertRaises(AppError):
            parse_recipients("not-an-email")

    def test_parse_recipients_allows_lists_over_fifty(self) -> None:
        raw = "\n".join(f"person{i}@example.com" for i in range(60))
        people = parse_recipients(raw)
        self.assertEqual(len(people), 60)

    def test_hiring_templates_render_html_and_plain_text(self) -> None:
        templates = list_templates()
        self.assertEqual(len(templates), 10)
        self.assertEqual(templates[0]["text_path"], "templates/hiring/hiring.txt")
        with tempfile.TemporaryDirectory() as folder:
            written = write_template_files(Path(folder))
            self.assertEqual(len(written), 12)
            text_path = Path(folder) / "templates" / "hiring" / "hiring.txt"
            self.assertTrue(text_path.is_file())
            plain_file = text_path.read_text(encoding="utf-8")
            self.assertIn("I came across your profile", plain_file)
            self.assertIn("50% of the earnings", plain_file)
            self.assertNotIn("<h1", plain_file)
        sample = get_template("horizon")
        assert sample is not None
        self.assertEqual(sample["subject"], SUBJECT)
        self.assertIn("<h1", sample["html"])
        self.assertIn("Account Executive Manager", sample["html"])
        self.assertIn("Apply Now", sample["html"])
        self.assertIn("https://forms.gle/d5ifR5Htk7jwRxmC9", sample["html"])
        self.assertIn("/static/hirelens-logo.png", sample["html"])
        self.assertIn('alt="HireLens"', sample["html"])
        self.assertNotIn("&lt;h1", sample["html"])
        self.assertIn("I came across your profile", sample["text"])
        self.assertIn("secure additional accounts", sample["text"])
        self.assertIn("50% of the earnings", sample["text"])
        self.assertNotIn("<", sample["text"])

    def test_upload_csv_and_json_recipient_lists(self) -> None:
        csv_result = parse_recipient_upload(
            "people.csv",
            "email,name\nada@example.com,Ada Lovelace\nGrace Hopper <grace@example.com>,\n",
        )
        self.assertEqual(csv_result["count"], 2)
        self.assertIn("Ada Lovelace <ada@example.com>", csv_result["to_text"])
        self.assertIn("grace@example.com", csv_result["to_text"])

        json_result = parse_recipient_upload(
            "people.json",
            json.dumps(
                {
                    "recipients": [
                        "first@example.com",
                        {"email": "second@example.com", "name": "Second Person"},
                    ]
                }
            ),
        )
        self.assertEqual(json_result["count"], 2)
        self.assertEqual(
            json_result["to_text"],
            "first@example.com\nSecond Person <second@example.com>",
        )

        array_result = parse_recipient_upload(
            "list.json",
            '["one@example.com", "two@example.com"]',
        )
        self.assertEqual(array_result["count"], 2)

    def test_form_overrides_build_settings_and_send(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            loaded = settings(root, smtp_password="", physical_address="")
            live = settings_with_overrides(
                loaded,
                email="davidyang@hirelens.us",
                password="secret",
                from_name="HireLens",
                physical_address="1 Market St",
            )
            self.assertEqual(live.smtp_password, "secret")
            self.assertEqual(live.physical_address, "1 Market St")
            message = build_compose_message(
                live,
                to_email="ada@example.com",
                to_name="Ada",
                subject="Hello",
                body="Body text",
            )
            self.assertEqual(message["Subject"], "Hello")
            self.assertIn("Body text", message.get_body(preferencelist=("plain",)).get_content())
            self.assertIn("1 Market St", message.get_body(preferencelist=("plain",)).get_content())
            html = message.get_body(preferencelist=("html",)).get_content()
            self.assertIn("Body text", html)

    def test_compose_attaches_inline_hirelens_logo(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder))
            sample = get_template("horizon")
            assert sample is not None
            message = build_compose_message(
                loaded,
                to_email="ada@example.com",
                to_name="Ada",
                subject=sample["subject"],
                body=sample["html"],
                body_format="html",
            )
            html_part = message.get_body(preferencelist=("html",))
            assert html_part is not None
            html = html_part.get_content()
            self.assertIn("cid:hirelens-logo", html)
            self.assertNotIn("/static/hirelens-logo.png", html)
            png_parts = [
                part
                for part in message.walk()
                if part.get_content_type() == "image/png"
            ]
            self.assertEqual(len(png_parts), 1)
            self.assertEqual(png_parts[0].get("Content-ID"), "<hirelens-logo>")
            self.assertEqual(png_parts[0].get_filename(), "hirelens-logo.png")

    def test_html_content_is_sent_as_markup(self) -> None:
        self.assertEqual(html_to_text("<h1>Hello</h1><p>Ada &amp; Grace</p>"), "Hello\nAda & Grace")
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder))
            message = build_compose_message(
                loaded,
                to_email="ada@example.com",
                to_name="Ada",
                subject="Hello",
                body='<h1>Welcome</h1><p>Click <a href="https://hirelens.us">here</a>.</p><script>alert(1)</script>',
                body_format="html",
            )
            html = message.get_body(preferencelist=("html",)).get_content()
            text = message.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("<h1>Welcome</h1>", html)
            self.assertNotIn("&lt;h1&gt;", html)
            self.assertIn('href="https://hirelens.us"', html)
            self.assertNotIn("<script>", html)
            self.assertIn("Welcome", text)
            self.assertIn("Click here.", text)
            self.assertNotIn("<h1>", text)
            # Markup pasted while "plain" is selected should still render as HTML.
            self.assertEqual(resolve_body_format("<p>Hi</p>", "plain"), "html")
            promoted = build_compose_message(
                loaded,
                to_email="ada@example.com",
                to_name="Ada",
                subject="Hello",
                body="<p>Hi <strong>there</strong></p>",
                body_format="plain",
            )
            promoted_html = promoted.get_body(preferencelist=("html",)).get_content()
            promoted_text = promoted.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("<p>Hi <strong>there</strong></p>", promoted_html)
            self.assertIn("Hi there", promoted_text)
            escaped = build_compose_message(
                loaded,
                to_email="ada@example.com",
                to_name="Ada",
                subject="Hello",
                body="Price is < 5 dollars",
                body_format="plain",
            )
            plain_html = escaped.get_body(preferencelist=("html",)).get_content()
            self.assertIn("Price is &lt; 5 dollars", plain_html)

    def test_settings_require_password(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder), smtp_password="")
            with self.assertRaises(ConfigError):
                settings_with_overrides(loaded, email="davidyang@hirelens.us", password="")

    def test_idle_time_waits_between_emails(self) -> None:
        self.assertEqual(parse_idle_seconds("12"), 12)
        with self.assertRaises(AppError):
            parse_idle_seconds(-1)
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder))
            waits: list[float] = []
            sent: list[str] = []

            class FakeMailer:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def send(self, message, recipient: str) -> None:
                    sent.append(recipient)

            import mailbot.webapp as webapp

            events: list[str] = []
            original = webapp.SmtpMailer
            webapp.SmtpMailer = lambda settings: FakeMailer()
            try:
                report = send_compose(
                    loaded,
                    recipients=[("a@example.com", ""), ("b@example.com", ""), ("c@example.com", "")],
                    subject="Hi",
                    body="Body",
                    idle_seconds=7,
                    sleep=waits.append,
                    on_progress=lambda event: events.append(event["type"]),
                )
            finally:
                webapp.SmtpMailer = original
            self.assertEqual(report["sent"], 3)
            self.assertEqual(waits, [7, 7])
            self.assertEqual(sent, ["a@example.com", "b@example.com", "c@example.com"])
            self.assertEqual(
                events,
                [
                    "start",
                    "sending",
                    "sent",
                    "waiting",
                    "sending",
                    "sent",
                    "waiting",
                    "sending",
                    "sent",
                    "done",
                ],
            )

    def test_stop_halts_after_current_email(self) -> None:
        self.assertTrue(wait_idle(1, lambda _seconds: None, should_stop=lambda: True))
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder))
            sent: list[str] = []

            class FakeMailer:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def send(self, message, recipient: str) -> None:
                    sent.append(recipient)

            import mailbot.webapp as webapp

            original = webapp.SmtpMailer
            webapp.SmtpMailer = lambda settings: FakeMailer()
            stop_after_first = {"armed": False}

            def should_stop() -> bool:
                return stop_after_first["armed"] and len(sent) >= 1

            try:
                report = send_compose(
                    loaded,
                    recipients=[("a@example.com", ""), ("b@example.com", ""), ("c@example.com", "")],
                    subject="Hi",
                    body="Body",
                    idle_seconds=5,
                    sleep=lambda _seconds: stop_after_first.update(armed=True),
                    should_stop=should_stop,
                )
            finally:
                webapp.SmtpMailer = original
            self.assertTrue(report["stopped"])
            self.assertEqual(report["sent"], 1)
            self.assertEqual(sent, ["a@example.com"])
            self.assertIn("Stopped", report["error"])

    def test_random_style_picks_html_per_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder))
            bodies: list[str] = []
            style_events: list[str] = []

            class FakeMailer:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def send(self, message, recipient: str) -> None:
                    html = message.get_body(preferencelist=("html",))
                    assert html is not None
                    bodies.append(html.get_content())

            import mailbot.webapp as webapp

            original = webapp.SmtpMailer
            webapp.SmtpMailer = lambda settings: FakeMailer()
            try:
                report = send_compose(
                    loaded,
                    recipients=[("a@example.com", ""), ("b@example.com", ""), ("c@example.com", "")],
                    subject="",
                    body="",
                    random_style=True,
                    idle_seconds=0,
                    rng=random.Random(7),
                    on_progress=lambda event: style_events.append(event.get("style_id", "")),
                )
            finally:
                webapp.SmtpMailer = original

            self.assertTrue(report["ok"])
            self.assertEqual(report["sent"], 3)
            self.assertTrue(report["random_style"])
            self.assertEqual(len(report["styles_used"]), 3)
            self.assertEqual(len(bodies), 3)
            self.assertEqual(len({item["id"] for item in report["styles_used"]}), 3)
            for body in bodies:
                self.assertIn("Apply Now", body)
                self.assertIn("Account Executive Manager", body)
            self.assertTrue(any(style_events))

    def test_pick_random_template_uses_rng(self) -> None:
        first = pick_random_template(rng=random.Random(3))
        second = pick_random_template(rng=random.Random(3))
        self.assertEqual(first["id"], second["id"])
        self.assertIn("<h1", first["html"])


class TemplateRenderTests(unittest.TestCase):
    def test_shipped_intro_template_renders(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            loaded = settings(Path(folder), public_base_url="")
            with Store(Path(folder) / "mail.db") as store:
                person, _outcome = store.add_subscriber(
                    "ada@example.com",
                    name="Ada Lovelace",
                    consent_source="form",
                )
            assert person is not None
            message = build_message(
                loaded,
                person,
                subject_template="Hi {{first_name}}",
                html_template=(ROOT / "templates" / "intro.html").read_text(encoding="utf-8"),
                text_template=(ROOT / "templates" / "intro.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(message["Subject"], "Hi Ada")
            self.assertNotIn("List-Unsubscribe-Post", message)
            self.assertIn("mailto:davidyang@hirelens.us", message["List-Unsubscribe"])


def _get(url: str) -> str:
    last: Exception | None = None
    for _ in range(20):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.read().decode()
        except urllib.error.URLError as exc:
            last = exc
            time.sleep(0.05)
    assert last is not None
    raise last


if __name__ == "__main__":
    unittest.main()
