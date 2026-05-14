"""Tests for the flexible time parser."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from clockify_mcp.errors import ValidationError
from clockify_mcp.time_parsing import format_iso_z, parse_to_utc

ZURICH = "Europe/Zurich"
NOW_UTC = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
NOW_ZURICH = NOW_UTC.astimezone(ZoneInfo(ZURICH))  # 14:00 local in May (CEST)


def test_now_returns_caller_now() -> None:
    result = parse_to_utc("now", tz=ZURICH, now=NOW_ZURICH)
    assert result == NOW_UTC


def test_iso_with_z_passes_through() -> None:
    result = parse_to_utc("2026-05-13T09:30:00Z", tz=ZURICH)
    assert result == datetime(2026, 5, 13, 9, 30, 0, tzinfo=UTC)


def test_iso_with_offset() -> None:
    result = parse_to_utc("2026-05-13T11:30:00+02:00", tz=ZURICH)
    assert result == datetime(2026, 5, 13, 9, 30, 0, tzinfo=UTC)


def test_iso_without_tz_uses_tz_arg() -> None:
    result = parse_to_utc("2026-05-13T09:30:00", tz=ZURICH)
    assert result == datetime(2026, 5, 13, 7, 30, 0, tzinfo=UTC)


def test_today_with_time() -> None:
    result = parse_to_utc("today 09:30", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 13, 7, 30, 0, tzinfo=UTC)


def test_today_without_time_is_midnight() -> None:
    result = parse_to_utc("today", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 12, 22, 0, 0, tzinfo=UTC)


def test_yesterday_with_time() -> None:
    result = parse_to_utc("yesterday 14:30", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 12, 12, 30, 0, tzinfo=UTC)


def test_relative_hours_ago() -> None:
    result = parse_to_utc("2h ago", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)


def test_relative_minutes_ago() -> None:
    result = parse_to_utc("45m ago", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 13, 11, 15, 0, tzinfo=UTC)


def test_hhmm_only_is_today_in_tz() -> None:
    result = parse_to_utc("09:30", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 13, 7, 30, 0, tzinfo=UTC)


def test_bare_date_is_midnight_in_tz() -> None:
    result = parse_to_utc("2026-05-13", tz=ZURICH, now=NOW_ZURICH)
    assert result == datetime(2026, 5, 12, 22, 0, 0, tzinfo=UTC)


def test_empty_raises() -> None:
    with pytest.raises(ValidationError):
        parse_to_utc("", tz=ZURICH)


def test_unparseable_raises() -> None:
    with pytest.raises(ValidationError):
        parse_to_utc("not a date at all", tz=ZURICH)


def test_unknown_tz_raises() -> None:
    with pytest.raises(ValidationError):
        parse_to_utc("today", tz="Not/A_Zone", now=NOW_UTC)


def test_format_iso_z_strips_microseconds() -> None:
    dt = datetime(2026, 5, 13, 9, 30, 15, 999, tzinfo=UTC)
    assert format_iso_z(dt) == "2026-05-13T09:30:15Z"


def test_format_iso_z_converts_offset_to_utc() -> None:
    dt = datetime(2026, 5, 13, 11, 30, 0, tzinfo=ZoneInfo(ZURICH))
    assert format_iso_z(dt) == "2026-05-13T09:30:00Z"


def test_round_trip() -> None:
    s = format_iso_z(parse_to_utc("today 09:30", tz=ZURICH, now=NOW_ZURICH))
    assert s == "2026-05-13T07:30:00Z"
