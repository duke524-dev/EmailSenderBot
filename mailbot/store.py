"""SQLite list of subscribers and delivery history."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_urlsafe


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HEADER_RE = re.compile(r"[^a-z0-9]+")
RESERVED_COLUMNS = {"email", "name", "company", "status"}
COLUMN_ALIASES = {
    "e_mail": "email",
    "email_address": "email",
    "full_name": "name",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def normalize_email(value: str) -> str:
    return value.strip().lower()


def valid_email(value: str) -> bool:
    return EMAIL_RE.fullmatch(value) is not None


def normalize_header(value: str) -> str:
    key = HEADER_RE.sub("_", value.strip().lower()).strip("_")
    return COLUMN_ALIASES.get(key, key)


@dataclass
class Subscriber:
    id: int
    email: str
    name: str
    company: str
    status: str
    consent_source: str
    unsubscribe_token: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def subscribed(self) -> bool:
        return self.status == "subscribed"


@dataclass
class ImportResult:
    added: int = 0
    duplicates: int = 0
    suppressed: int = 0
    invalid: int = 0
    invalid_rows: list[str] = field(default_factory=list)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('subscribed', 'unsubscribed')),
                consent_source TEXT NOT NULL,
                unsubscribe_token TEXT NOT NULL UNIQUE,
                fields_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                unsubscribed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                subject TEXT NOT NULL,
                html_template TEXT NOT NULL,
                text_template TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY,
                campaign_id INTEGER REFERENCES campaigns(id),
                subscriber_id INTEGER REFERENCES subscribers(id),
                recipient TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
                error TEXT,
                message_id TEXT,
                sent_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS deliveries_sent_at ON deliveries (sent_at);
            CREATE INDEX IF NOT EXISTS deliveries_campaign ON deliveries (campaign_id, subscriber_id, status);
            """
        )
        self._conn.commit()

    def add_subscriber(
        self,
        email: str,
        *,
        name: str = "",
        company: str = "",
        consent_source: str,
        fields: dict[str, str] | None = None,
        status: str = "subscribed",
    ) -> tuple[Subscriber | None, str]:
        """Return the row and a result: added, duplicate, suppressed, or invalid."""
        source = consent_source.strip()
        if not source:
            raise ValueError("A consent source is required, such as where this person opted in.")
        if status not in {"subscribed", "unsubscribed"}:
            raise ValueError("Status must be subscribed or unsubscribed.")
        address = normalize_email(email)
        if not valid_email(address):
            return None, "invalid"

        existing = self.get_by_email(address)
        if existing is not None:
            if existing.status == "unsubscribed":
                return existing, "suppressed"
            return self._refresh_subscriber(existing, name, company, fields or {}), "duplicate"

        now = iso(utcnow())
        unsubscribed_at = now if status == "unsubscribed" else None
        cursor = self._conn.execute(
            """
            INSERT INTO subscribers (
                email, name, company, status, consent_source,
                unsubscribe_token, fields_json, created_at, unsubscribed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                address,
                name.strip(),
                company.strip(),
                status,
                source,
                token_urlsafe(24),
                json.dumps(fields or {}, separators=(",", ":")),
                now,
                unsubscribed_at,
            ),
        )
        self._conn.commit()
        return self.get_subscriber(int(cursor.lastrowid)), "added"

    def _refresh_subscriber(
        self,
        existing: Subscriber,
        name: str,
        company: str,
        fields: dict[str, str],
    ) -> Subscriber:
        new_name = name.strip() or existing.name
        new_company = company.strip() or existing.company
        merged = dict(existing.fields)
        changed = new_name != existing.name or new_company != existing.company
        for key, value in fields.items():
            if value and merged.get(key) != value:
                merged[key] = value
                changed = True
        if not changed:
            return existing
        self._conn.execute(
            "UPDATE subscribers SET name = ?, company = ?, fields_json = ? WHERE id = ?",
            (new_name, new_company, json.dumps(merged, separators=(",", ":")), existing.id),
        )
        self._conn.commit()
        refreshed = self.get_subscriber(existing.id)
        assert refreshed is not None
        return refreshed

    def import_csv(self, path: Path, *, consent_source: str) -> ImportResult:
        result = ImportResult()
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "email" not in {normalize_header(name) for name in reader.fieldnames}:
                raise ValueError(f"{path} needs a header row with an email column.")
            for line_number, row in enumerate(reader, start=2):
                mapped = {normalize_header(key): (value or "").strip() for key, value in row.items() if key}
                email = mapped.get("email", "")
                status = mapped.get("status", "subscribed").lower() or "subscribed"
                if status in {"unsubscribe", "removed"}:
                    status = "unsubscribed"
                if status not in {"subscribed", "unsubscribed"}:
                    result.invalid += 1
                    result.invalid_rows.append(f"line {line_number}: status must be subscribed or unsubscribed")
                    continue
                extras = {
                    key: value
                    for key, value in mapped.items()
                    if key not in RESERVED_COLUMNS and key not in {"consent_source"} and value
                }
                try:
                    _row, outcome = self.add_subscriber(
                        email,
                        name=mapped.get("name", ""),
                        company=mapped.get("company", ""),
                        consent_source=mapped.get("consent_source") or consent_source,
                        fields=extras,
                        status=status,
                    )
                except ValueError as exc:
                    result.invalid += 1
                    result.invalid_rows.append(f"line {line_number}: {exc}")
                    continue
                if outcome == "added":
                    result.added += 1
                elif outcome == "duplicate":
                    result.duplicates += 1
                elif outcome == "suppressed":
                    result.suppressed += 1
                else:
                    result.invalid += 1
                    result.invalid_rows.append(f"line {line_number}: {email or '(blank)'} is not an email address")
        return result

    def get_subscriber(self, subscriber_id: int) -> Subscriber | None:
        row = self._conn.execute("SELECT * FROM subscribers WHERE id = ?", (subscriber_id,)).fetchone()
        return _subscriber(row) if row else None

    def get_by_email(self, email: str) -> Subscriber | None:
        row = self._conn.execute(
            "SELECT * FROM subscribers WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()
        return _subscriber(row) if row else None

    def get_by_token(self, token: str) -> Subscriber | None:
        row = self._conn.execute(
            "SELECT * FROM subscribers WHERE unsubscribe_token = ?",
            (token,),
        ).fetchone()
        return _subscriber(row) if row else None

    def list_subscribers(self, status: str | None = None) -> list[Subscriber]:
        if status is None:
            rows = self._conn.execute("SELECT * FROM subscribers ORDER BY email").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM subscribers WHERE status = ? ORDER BY email",
                (status,),
            ).fetchall()
        return [_subscriber(row) for row in rows]

    def unsubscribe(self, *, email: str | None = None, token: str | None = None) -> Subscriber | None:
        row = self.get_by_token(token) if token else self.get_by_email(email or "")
        if row is None:
            return None
        if row.status == "subscribed":
            self._conn.execute(
                """
                UPDATE subscribers
                SET status = 'unsubscribed', unsubscribed_at = ?
                WHERE id = ?
                """,
                (iso(utcnow()), row.id),
            )
            self._conn.commit()
        return self.get_subscriber(row.id)

    def remove_subscriber(self, email: str) -> str:
        row = self.get_by_email(email)
        if row is None:
            return "missing"
        sent = self._conn.execute(
            """
            SELECT 1 FROM deliveries
            WHERE subscriber_id = ? AND status = 'sent'
            LIMIT 1
            """,
            (row.id,),
        ).fetchone()
        if sent:
            self.unsubscribe(email=row.email)
            return "unsubscribed"
        self._conn.execute("DELETE FROM deliveries WHERE subscriber_id = ?", (row.id,))
        self._conn.execute("DELETE FROM subscribers WHERE id = ?", (row.id,))
        self._conn.commit()
        return "deleted"

    def upsert_campaign(self, name: str, subject: str, html_template: str, text_template: str) -> int:
        now = iso(utcnow())
        self._conn.execute(
            """
            INSERT INTO campaigns (name, subject, html_template, text_template, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                subject = excluded.subject,
                html_template = excluded.html_template,
                text_template = excluded.text_template
            """,
            (name, subject, html_template, text_template, now),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT id FROM campaigns WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def campaign_id(self, name: str) -> int | None:
        row = self._conn.execute("SELECT id FROM campaigns WHERE name = ?", (name,)).fetchone()
        return int(row["id"]) if row else None

    def pending_recipients(self, campaign_id: int) -> list[Subscriber]:
        rows = self._conn.execute(
            """
            SELECT s.*
            FROM subscribers s
            WHERE s.status = 'subscribed'
              AND NOT EXISTS (
                  SELECT 1 FROM deliveries d
                  WHERE d.campaign_id = ?
                    AND d.subscriber_id = s.id
                    AND d.status = 'sent'
              )
            ORDER BY s.email
            """,
            (campaign_id,),
        ).fetchall()
        return [_subscriber(row) for row in rows]

    def record_delivery(
        self,
        *,
        recipient: str,
        status: str,
        campaign_id: int | None = None,
        subscriber_id: int | None = None,
        error: str | None = None,
        message_id: str | None = None,
        sent_at: datetime | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO deliveries (
                campaign_id, subscriber_id, recipient, status, error, message_id, sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                subscriber_id,
                normalize_email(recipient),
                status,
                error,
                message_id,
                iso(sent_at or utcnow()),
            ),
        )
        self._conn.commit()

    def recent_sent(self, since: datetime) -> list[datetime]:
        rows = self._conn.execute(
            """
            SELECT sent_at FROM deliveries
            WHERE status = 'sent' AND sent_at > ?
            ORDER BY sent_at
            """,
            (iso(since),),
        ).fetchall()
        return [parse_iso(row["sent_at"]) for row in rows]

    def campaign_counts(self, campaign_id: int) -> dict[str, int]:
        rows = self._conn.execute(
            """
            SELECT status, COUNT(*) AS n
            FROM deliveries
            WHERE campaign_id = ?
            GROUP BY status
            """,
            (campaign_id,),
        ).fetchall()
        counts = {"sent": 0, "failed": 0}
        counts.update({row["status"]: int(row["n"]) for row in rows})
        return counts


def _subscriber(row: sqlite3.Row) -> Subscriber:
    try:
        fields = json.loads(row["fields_json"])
    except json.JSONDecodeError:
        fields = {}
    if not isinstance(fields, dict):
        fields = {}
    return Subscriber(
        id=int(row["id"]),
        email=row["email"],
        name=row["name"],
        company=row["company"],
        status=row["status"],
        consent_source=row["consent_source"],
        unsubscribe_token=row["unsubscribe_token"],
        fields={str(key): str(value) for key, value in fields.items()},
    )


def window_start(now: datetime) -> datetime:
    return now - timedelta(hours=1)
