"""SMTP connection to Namecheap Private Email."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from mailbot.config import Settings


TEMPORARY_CODES = {421, 450, 451, 452, 454}


class MailerError(Exception):
    """The SMTP server could not take mail."""


class AuthFailed(MailerError):
    """The mailbox rejected the username or password."""


class TemporaryFailure(MailerError):
    """The server asked the client to wait and try again."""


class RecipientRejected(MailerError):
    """The server refused this recipient."""


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class SmtpMailer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._smtp: smtplib.SMTP | None = None

    def __enter__(self) -> SmtpMailer:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        settings = self.settings
        try:
            if settings.smtp_security == "ssl":
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=20,
                )
            else:
                smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
        except smtplib.SMTPAuthenticationError as exc:
            raise AuthFailed(
                "SMTP login failed. Check SMTP_USERNAME and SMTP_PASSWORD. "
                "In Private Email, turn on SMTP under Connect third-party apps. "
                "If an app password exists, use that password here. "
                f"Server said: {_text(exc.smtp_error)}"
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise MailerError(
                f"Could not connect to {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security}). {exc}"
            ) from exc
        self._smtp = smtp

    def close(self) -> None:
        smtp = self._smtp
        self._smtp = None
        if smtp is None:
            return
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            try:
                smtp.close()
            except (OSError, smtplib.SMTPException):
                pass

    def send(self, message: EmailMessage, recipient: str) -> None:
        self._send_once(message, recipient, reconnect=True)

    def _send_once(self, message: EmailMessage, recipient: str, *, reconnect: bool) -> None:
        if self._smtp is None:
            self.connect()
        assert self._smtp is not None
        try:
            self._smtp.send_message(
                message,
                from_addr=self.settings.smtp_username,
                to_addrs=[recipient],
            )
        except smtplib.SMTPServerDisconnected:
            if not reconnect:
                raise MailerError("The SMTP server closed the connection.")
            self._smtp = None
            self.connect()
            self._send_once(message, recipient, reconnect=False)
        except smtplib.SMTPRecipientsRefused as exc:
            code, detail = next(iter(exc.recipients.values()))
            _raise_for_code(int(code), _text(detail))
        except smtplib.SMTPResponseException as exc:
            _raise_for_code(int(exc.smtp_code), _text(exc.smtp_error))


def _raise_for_code(code: int, detail: str) -> None:
    message = f"{code} {detail}".strip()
    if code in {534, 535, 530}:
        raise AuthFailed(message)
    if code in TEMPORARY_CODES:
        raise TemporaryFailure(message)
    raise RecipientRejected(message)
