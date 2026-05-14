"""Parse flexible date/time strings into UTC datetimes / Clockify-format strings.

Accepts:
  - ISO-8601 (with or without offset)
  - `"now"`, `"today HH:MM"`, `"yesterday HH:MM"`
  - `"Nh ago"`, `"Nm ago"` (also `"N hours ago"`, `"N min ago"`)
  - `"HH:MM"` (today at that time in user timezone)
  - bare dates like `"2026-05-13"` → midnight in user timezone
  - anything dateutil.parser can handle as a final fallback

Naive inputs are interpreted in the caller-supplied IANA timezone.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser as dateparser

from .errors import ValidationError

_RELATIVE_AGO = re.compile(r"^(?P<n>\d+)\s*(?P<unit>h(?:ours?)?|m(?:in(?:utes?)?)?)\s+ago$")
_TODAY_YESTERDAY = re.compile(r"^(?P<day>today|yesterday)(?:\s+(?P<hh>\d{1,2}):(?P<mm>\d{2}))?$")
_HHMM_ONLY = re.compile(r"^(?P<hh>\d{1,2}):(?P<mm>\d{2})$")


def parse_to_utc(
    value: str | datetime,
    tz: str = "UTC",
    *,
    now: datetime | None = None,
) -> datetime:
    """Parse `value` and return an aware UTC datetime.

    `tz` is the IANA timezone used to interpret naive / relative inputs.
    `now` is injectable for testing.
    """
    if isinstance(value, datetime):
        return _to_utc(value, tz)

    s = value.strip()
    if not s:
        raise ValidationError("Empty time string")

    zone = _zone(tz)
    now = now or datetime.now(zone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=zone)

    s_lower = s.lower()

    if s_lower == "now":
        return _to_utc(now, tz)

    m = _RELATIVE_AGO.match(s_lower)
    if m:
        n = int(m.group("n"))
        unit = m.group("unit")
        delta = timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
        return _to_utc(now - delta, tz)

    m = _TODAY_YESTERDAY.match(s_lower)
    if m:
        day_word = m.group("day")
        hh_raw, mm_raw = m.group("hh"), m.group("mm")
        if hh_raw is None:
            base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            base = now.replace(hour=int(hh_raw), minute=int(mm_raw), second=0, microsecond=0)
        if day_word == "yesterday":
            base -= timedelta(days=1)
        return _to_utc(base, tz)

    m = _HHMM_ONLY.match(s)
    if m:
        base = now.replace(
            hour=int(m.group("hh")),
            minute=int(m.group("mm")),
            second=0,
            microsecond=0,
        )
        return _to_utc(base, tz)

    try:
        dt = dateparser.parse(s)
    except (ValueError, OverflowError) as exc:
        raise ValidationError(f"Could not parse time string: {value!r}") from exc
    if dt is None:
        raise ValidationError(f"Could not parse time string: {value!r}")
    return _to_utc(dt, tz)


def format_iso_z(dt: datetime) -> str:
    """ISO-8601 UTC with `Z` suffix (Clockify's required format)."""
    dt = dt.astimezone(UTC).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_utc(dt: datetime, tz: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_zone(tz))
    return dt.astimezone(UTC)


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"Unknown timezone: {tz!r}") from exc
