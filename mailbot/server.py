"""Unsubscribe routes. The full web UI lives in mailbot.webapp."""

from __future__ import annotations

from pathlib import Path

from mailbot.webapp import make_handler as make_web_handler
from mailbot.webapp import serve as serve_webapp


def make_handler(database: Path, from_name: str):
    """Keep the old unsubscribe-only constructor used by tests."""
    from mailbot.config import Settings

    settings = Settings(
        smtp_host="mail.privateemail.com",
        smtp_port=465,
        smtp_security="ssl",
        smtp_username="unused@example.com",
        smtp_password="",
        from_name=from_name,
        from_email="unused@example.com",
        reply_to="unused@example.com",
        hourly_limit=15,
        physical_address="",
        public_base_url="",
        apply_url="https://forms.gle/d5ifR5Htk7jwRxmC9",
        database_path=database,
        env_path=Path(".env"),
    )
    # The web handler serves the compose UI from web/. For isolated unsubscribe
    # tests, point at a temporary empty web root that only needs /u/ routes.
    web_root = Path(__file__).resolve().parent.parent / "web"
    return make_web_handler(settings, web_root)


def serve(database: Path, from_name: str, host: str, port: int) -> None:
    del database, from_name
    serve_webapp(host=host, port=port)
