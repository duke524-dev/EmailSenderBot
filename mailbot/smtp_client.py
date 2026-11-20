"""SMTP connection for mailbox providers such as IONOS and Private Email."""

from __future__ import annotations

import smtplib
import ssl
import time
from email.message import EmailMessage

from mailbot.config import Settings


TEMPORARY_CODES = {421, 450, 451, 452, 454}
CONNECT_ATTEMPTS = 2
CONNECT_RETRY_SECONDS = (0, 20)
SMTP_TIMEOUT_SECONDS = 45


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


def _is_transient_connect_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "timed out",
            "timeout",
            "temporarily",
            "try again",
            "connection reset",
            "connection refused",
            "network is unreachable",
            "handshake",
        )
    )


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
        last_error: BaseException | None = None
        for attempt, pause in enumerate(CONNECT_RETRY_SECONDS[:CONNECT_ATTEMPTS]):
            if pause:
                time.sleep(pause)
            try:
                self._smtp = self._open_session()
                return
            except smtplib.SMTPAuthenticationError as exc:
                raise AuthFailed(
                    "SMTP login failed. Check SMTP_USERNAME and SMTP_PASSWORD. "
                    "Use the full mailbox address and its password (or app password). "
                    f"Server said: {_text(exc.smtp_error)}"
                ) from exc
            except (OSError, ssl.SSLError, smtplib.SMTPException) as exc:
                last_error = exc
                if not _is_transient_connect_error(exc):
                    break
                if attempt + 1 < CONNECT_ATTEMPTS:
                    continue
        assert last_error is not None
        detail = (
            f"Could not connect to {settings.smtp_host}:{settings.smtp_port} "
            f"({settings.smtp_security}). {last_error}"
        )
        if settings.smtp_port == 465:
            detail += " If this keeps timing out, try SMTP_PORT=587 and SMTP_SECURITY=starttls."
        if _is_transient_connect_error(last_error):
            raise TemporaryFailure(detail) from last_error
        raise MailerError(detail) from last_error

    def _open_session(self) -> smtplib.SMTP:
        settings = self.settings
        if settings.smtp_security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=SMTP_TIMEOUT_SECONDS,
            )
        else:
            smtp = smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=SMTP_TIMEOUT_SECONDS,
            )
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        smtp.login(settings.smtp_username, settings.smtp_password)
        return smtp

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
                raise TemporaryFailure("The SMTP server closed the connection.")
            self._smtp = None
            self.connect()
            self._send_once(message, recipient, reconnect=False)
        except (TimeoutError, OSError, ssl.SSLError) as exc:
            self._smtp = None
            if _is_transient_connect_error(exc):
                if reconnect:
                    try:
                        self.connect()
                        self._send_once(message, recipient, reconnect=False)
                        return
                    except TemporaryFailure:
                        raise
                raise TemporaryFailure(
                    f"SMTP connection failed while sending to {recipient}: {exc}"
                ) from exc
            raise MailerError(f"SMTP connection failed while sending to {recipient}: {exc}") from exc
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
