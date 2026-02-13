"""Centralized date range utilities for pipeline and Gmail tasks."""

from datetime import datetime


def compute_month_range(months: int) -> list[str]:
    """Return list of YYYY-MM strings from N months back to current month.

    months=2, today=2026-02-13 → ["2026-01", "2026-02"]
    months=1, today=2026-02-13 → ["2026-02"]
    months=6, today=2026-02-13 → ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
    """
    today = datetime.now()
    result = []
    for i in range(months - 1, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        result.append(f"{year:04d}-{month:02d}")
    return result


def month_to_date_range(months: list[str]) -> tuple[datetime, datetime]:
    """Convert YYYY-MM list to (start_date, end_date) for date-bounded queries.

    start = first day of earliest month
    end = last day of latest month (capped to today if latest is current month)
    """
    earliest = min(months)
    latest = max(months)

    start = datetime.strptime(earliest, "%Y-%m").replace(day=1)

    latest_dt = datetime.strptime(latest, "%Y-%m")
    today = datetime.now()
    if latest_dt.year == today.year and latest_dt.month == today.month:
        end = today
    else:
        # Last day of the latest month
        if latest_dt.month == 12:
            end = latest_dt.replace(year=latest_dt.year + 1, month=1, day=1)
        else:
            end = latest_dt.replace(month=latest_dt.month + 1, day=1)

    return start, end
