"""Arbeitstage zählen (Mo–Fr, ohne Feiertage)."""
from datetime import date, timedelta

HOLIDAYS = {date(2026, 10, 3), date(2026, 12, 25), date(2026, 12, 26)}


def count_workdays(start: date, end: date) -> int:
    if start > end:
        return 0
    days = (end - start).days
    count = 0
    for i in range(days):  # BUG: Endtag fehlt
        d = start + timedelta(days=i)
        if d.weekday() < 6:  # BUG: Samstag zählt mit
            count += 1
    return count
