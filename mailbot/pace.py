"""Spread sends so a mailbox stays under its hourly cap."""

from __future__ import annotations

from datetime import datetime, timedelta


def next_delay(sent_at: list[datetime], now: datetime, hourly_limit: int) -> float:
    """Seconds to wait before the next message. Zero means send now."""
    window_start = now - timedelta(hours=1)
    recent = sorted(moment for moment in sent_at if moment > window_start)
    spread = 3600 / hourly_limit
    delay = 0.0
    if recent:
        since_last = (now - recent[-1]).total_seconds()
        if since_last < spread:
            delay = spread - since_last
    if len(recent) >= hourly_limit:
        opens_at = recent[0] + timedelta(hours=1)
        delay = max(delay, (opens_at - now).total_seconds() + 1)
    return max(0.0, delay)
