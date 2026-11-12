"""Load mailbox settings from the environment and a local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """The local configuration cannot be used to send mail."""


def project_root() -> Path:
    override = os.environ.get("MAILBOT_ROOT", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _pick(key: str, file_values: dict[str, str], default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    return file_values.get(key, default)


@dataclass(frozen=True)
class Settings:
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str = field(repr=False)
    from_name: str
    from_email: str
    reply_to: str
    hourly_limit: int
    physical_address: str
    public_base_url: str
    apply_url: str
    database_path: Path
    env_path: Path

    def require_live_send(self) -> None:
        missing = []
        if not self.smtp_password:
            missing.append("SMTP_PASSWORD")
        if not self.physical_address:
            missing.append("PHYSICAL_ADDRESS")
        if missing:
            raise ConfigError(
                "Set " + " and ".join(missing) + f" in {self.env_path} before a live send."
            )
        if self.from_email.lower() != self.smtp_username.lower():
            raise ConfigError(
                "FROM_EMAIL must match SMTP_USERNAME. Private Email only sends as the mailbox you log in with."
            )
        if self.reply_to.lower() != self.from_email.lower():
            raise ConfigError("REPLY_TO must be the same mailbox as FROM_EMAIL.")


def load_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    env_path = root / ".env"
    file_values = load_env_file(env_path)

    security = _pick("SMTP_SECURITY", file_values, "ssl").strip().lower()
    if security not in {"ssl", "starttls"}:
        raise ConfigError("SMTP_SECURITY must be ssl or starttls.")

    try:
        port = int(_pick("SMTP_PORT", file_values, "465" if security == "ssl" else "587"))
    except ValueError as exc:
        raise ConfigError("SMTP_PORT must be a number.") from exc

    try:
        hourly_limit = int(_pick("HOURLY_LIMIT", file_values, "15"))
    except ValueError as exc:
        raise ConfigError("HOURLY_LIMIT must be a number.") from exc
    if not 1 <= hourly_limit <= 500:
        raise ConfigError(
            "HOURLY_LIMIT must be from 1 to 500. Paid Private Email allows 500 per hour per mailbox; trial plans allow 20."
        )

    username = _pick("SMTP_USERNAME", file_values, "davidyang@hirelens.us").strip()
    from_email = _pick("FROM_EMAIL", file_values, username).strip()
    reply_to = _pick("REPLY_TO", file_values, from_email).strip()
    database = _pick("DATABASE_PATH", file_values, "").strip()
    database_path = Path(database) if database else root / "data" / "mailbot.db"
    if not database_path.is_absolute():
        database_path = root / database_path

    public_base = _pick("PUBLIC_BASE_URL", file_values, "").strip().rstrip("/")
    if public_base and not public_base.startswith(("https://", "http://")):
        raise ConfigError("PUBLIC_BASE_URL must start with https:// or http://.")

    apply_url = _pick(
        "APPLY_URL",
        file_values,
        "https://forms.gle/d5ifR5Htk7jwRxmC9",
    ).strip()
    if apply_url and not apply_url.startswith(("https://", "http://")):
        raise ConfigError("APPLY_URL must start with https:// or http://.")

    return Settings(
        smtp_host=_pick("SMTP_HOST", file_values, "mail.privateemail.com").strip(),
        smtp_port=port,
        smtp_security=security,
        smtp_username=username,
        smtp_password=_pick("SMTP_PASSWORD", file_values, ""),
        from_name=_pick("FROM_NAME", file_values, "HireLens").strip() or "HireLens",
        from_email=from_email,
        reply_to=reply_to,
        hourly_limit=hourly_limit,
        physical_address=_pick("PHYSICAL_ADDRESS", file_values, "").strip(),
        public_base_url=public_base,
        apply_url=apply_url or "https://forms.gle/d5ifR5Htk7jwRxmC9",
        database_path=database_path,
        env_path=env_path,
    )


def settings_with_overrides(
    base: Settings | None = None,
    *,
    email: str = "",
    password: str = "",
    from_name: str = "",
    physical_address: str = "",
) -> Settings:
    """Build Settings for one send from the web form. Password stays in memory."""
    from dataclasses import replace

    settings = base or load_settings()
    address = email.strip() or settings.smtp_username
    if not address:
        raise ConfigError("Email address is required.")
    secret = password if password else settings.smtp_password
    if not secret:
        raise ConfigError("Password is required.")
    display = from_name.strip() or settings.from_name or "HireLens"
    postal = physical_address.strip() if physical_address.strip() else settings.physical_address
    return replace(
        settings,
        smtp_username=address,
        smtp_password=secret,
        from_email=address,
        reply_to=address,
        from_name=display,
        physical_address=postal,
    )
