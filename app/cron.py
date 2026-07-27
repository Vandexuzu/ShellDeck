"""Minimal 5-field cron parser (no external deps).

Supports: standard fields "m h dom mon dow", with comma-lists, ranges (a-b),
steps (*/n), and the wildcard *. Computes the next future fire time from a
given starting point (local machine time, matching the rest of the app).

Returns None if the expression is invalid or would never fire a valid minute.
"""
from __future__ import annotations

from datetime import datetime, timedelta


def _field_values(field: str, low: int, high: int) -> set[int] | None:
    """Expand a single cron field into the set of matching integer values."""
    if field.strip() == "*":
        return set(range(low, high + 1))
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            rng, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                return None
            if step < 1:
                return None
        else:
            rng = part
        if rng == "*":
            lo, hi = low, high
        elif "-" in rng:
            try:
                lo_s, hi_s = rng.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                return None
        else:
            try:
                val = int(rng)
            except ValueError:
                return None
            lo = hi = val
        if lo < low or hi > high or lo > hi:
            return None
        out.update(range(lo, hi + 1, step))
    return out


def _month_names(field: str) -> str:
    """Allow Jan-Dec / Mon-Fri names in month/dow fields."""
    names = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
    }
    for k, v in names.items():
        field = field.replace(k, str(v))
    return field


def parse_cron(expr: str) -> dict[str, set[int]] | None:
    """Parse a 5-field cron expression into minute/hour/dom/month/dow sets."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    parts[3] = _month_names(parts[3])
    parts[4] = _month_names(parts[4])
    minutes = _field_values(parts[0], 0, 59)
    hours = _field_values(parts[1], 0, 23)
    doms = _field_values(parts[2], 1, 31)
    months = _field_values(parts[3], 1, 12)
    dows = _field_values(parts[4], 0, 6)
    if None in (minutes, hours, doms, months, dows):
        return None
    return {"minute": minutes, "hour": hours, "dom": doms, "month": months, "dow": dows}


def next_fire(expr: str, after: datetime) -> datetime | None:
    """Return the next datetime >= (after + 1 minute) that matches `expr`.

    Honors the standard cron rule: a day matches if (dom matches) OR (dow matches),
    unless one of them is a wildcard (*), in which case that constraint is ignored.
    """
    fields = parse_cron(expr)
    if fields is None:
        return None
    # Day-of-week 7 == 0 (Sunday) convenience.
    fields["dow"] = {d % 7 for d in fields["dow"]}
    dom_wild = "*" in expr.split()[2].strip()
    dow_wild = "*" in expr.split()[4].strip()

    # Start scanning from the next minute.
    cur = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cur + timedelta(days=366 * 4)  # bound the search (4 years)
    while cur <= limit:
        if cur.month not in fields["month"]:
            # Jump to first day of next matching month.
            cur = _next_month(cur)
            continue
        dom_ok = cur.day in fields["dom"]
        dow_ok = cur.weekday() in fields["dow"]
        day_ok = dom_ok or dow_ok if (not dom_wild and not dow_wild) else (dom_ok or dow_ok)
        # If both constraints are wild, day always matches.
        if dom_wild and dow_wild:
            day_ok = True
        elif dom_wild:
            day_ok = dow_ok
        elif dow_wild:
            day_ok = dom_ok
        if not day_ok:
            cur = cur + timedelta(days=1)
            cur = cur.replace(hour=0, minute=0)
            continue
        if cur.hour not in fields["hour"] or cur.minute not in fields["minute"]:
            # Advance to next candidate minute (keep date).
            cur = cur + timedelta(minutes=1)
            if cur.hour == 0 and cur.minute == 0:
                cur = cur.replace(hour=0, minute=0)
            continue
        return cur
    return None


def _next_month(d: datetime) -> datetime:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return d.replace(month=d.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
