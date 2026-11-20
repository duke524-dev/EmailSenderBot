"""Persist an in-progress compose send so a crash can resume from the failed address."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()


def job_path_for(database_path: Path) -> Path:
    return Path(database_path).resolve().parent / "send_job.json"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def load_job(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_job(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = utcnow_iso()
    tmp = path.with_suffix(".json.tmp")
    with _LOCK:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)


def clear_job(path: Path) -> None:
    with _LOCK:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def resumable_job(path: Path) -> dict[str, Any] | None:
    job = load_job(path)
    if job is None:
        return None
    if job.get("status") not in {"paused", "running"}:
        return None
    recipients = job.get("recipients")
    next_index = int(job.get("next_index", 0) or 0)
    if not isinstance(recipients, list) or next_index >= len(recipients):
        return None
    remaining = recipients[next_index:]
    if not remaining:
        return None
    resume_email = ""
    first = remaining[0]
    if isinstance(first, (list, tuple)) and first:
        resume_email = str(first[0])
    elif isinstance(first, dict):
        resume_email = str(first.get("email", ""))
    return {
        "ok": True,
        "status": job.get("status"),
        "resume_email": job.get("resume_email") or resume_email,
        "next_index": next_index,
        "remaining": len(remaining),
        "planned": int(job.get("planned", len(recipients)) or 0),
        "sent": int(job.get("sent", 0) or 0),
        "failed": int(job.get("failed", 0) or 0),
        "subject": job.get("subject", ""),
        "body": job.get("body", ""),
        "body_format": job.get("body_format", "plain"),
        "random_style": bool(job.get("random_style", False)),
        "idle_seconds": int(job.get("idle_seconds", 0) or 0),
        "to_text": _recipients_to_text(remaining),
        "updated_at": job.get("updated_at", ""),
        "error": job.get("error", ""),
    }


def _recipients_to_text(recipients: list) -> str:
    lines: list[str] = []
    for item in recipients:
        if isinstance(item, dict):
            email = str(item.get("email", "")).strip()
            name = str(item.get("name", "")).strip()
        elif isinstance(item, (list, tuple)) and item:
            email = str(item[0]).strip()
            name = str(item[1]).strip() if len(item) > 1 else ""
        else:
            continue
        if not email:
            continue
        lines.append(f"{name} <{email}>" if name else email)
    return "\n".join(lines)


def recipients_from_job(job: dict[str, Any]) -> list[tuple[str, str]]:
    next_index = int(job.get("next_index", 0) or 0)
    people: list[tuple[str, str]] = []
    for item in job.get("recipients", [])[next_index:]:
        if isinstance(item, dict):
            email = str(item.get("email", "")).strip().lower()
            name = str(item.get("name", "")).strip()
        elif isinstance(item, (list, tuple)) and item:
            email = str(item[0]).strip().lower()
            name = str(item[1]).strip() if len(item) > 1 else ""
        else:
            continue
        if email:
            people.append((email, name))
    return people


def start_job(
    path: Path,
    *,
    recipients: list[tuple[str, str]],
    subject: str,
    body: str,
    body_format: str,
    random_style: bool,
    idle_seconds: int,
) -> dict[str, Any]:
    payload = {
        "status": "running",
        "recipients": [{"email": email, "name": name} for email, name in recipients],
        "next_index": 0,
        "resume_email": recipients[0][0] if recipients else "",
        "planned": len(recipients),
        "sent": 0,
        "failed": 0,
        "subject": subject,
        "body": body,
        "body_format": body_format,
        "random_style": random_style,
        "idle_seconds": idle_seconds,
        "error": "",
    }
    save_job(path, payload)
    return payload


def mark_sent(path: Path, *, index: int, sent: int, failed: int) -> None:
    job = load_job(path)
    if job is None:
        return
    job["status"] = "running"
    job["next_index"] = index
    job["sent"] = sent
    job["failed"] = failed
    remaining = job.get("recipients", [])[index:]
    if remaining:
        first = remaining[0]
        job["resume_email"] = (
            first.get("email", "") if isinstance(first, dict) else str(first[0])
        )
    else:
        job["resume_email"] = ""
    job["error"] = ""
    save_job(path, job)


def mark_paused(
    path: Path,
    *,
    next_index: int,
    resume_email: str,
    sent: int,
    failed: int,
    error: str,
) -> None:
    job = load_job(path)
    if job is None:
        return
    job["status"] = "paused"
    job["next_index"] = max(0, next_index)
    job["resume_email"] = resume_email
    job["sent"] = sent
    job["failed"] = failed
    job["error"] = error
    save_job(path, job)


def mark_done(path: Path) -> None:
    clear_job(path)
