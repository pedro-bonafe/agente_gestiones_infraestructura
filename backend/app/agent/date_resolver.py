"""
Converts relative date expressions to absolute ISO date strings.
All resolution is done relative to today's date (UTC).
"""

import re
from datetime import date, timedelta


def _today() -> date:
    from datetime import datetime
    return datetime.utcnow().date()


def resolve_dates(fecha_desde: str | None, fecha_hasta: str | None) -> tuple[str | None, str | None]:
    """
    Resolve raw date strings (possibly relative) to absolute ISO date strings (YYYY-MM-DD).
    Returns (fecha_desde_iso, fecha_hasta_iso).
    If a value is already ISO format or None, it passes through unchanged.
    """
    return _resolve(fecha_desde), _resolve(fecha_hasta)


def _resolve(expr: str | None) -> str | None:
    if not expr:
        return None
    expr = expr.strip().lower()

    # Already ISO date: YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", expr):
        return expr

    today = _today()

    # "hoy"
    if expr in ("hoy", "today"):
        return today.isoformat()

    # "ayer"
    if expr in ("ayer", "yesterday"):
        return (today - timedelta(days=1)).isoformat()

    # "este mes" / "este año"
    if expr in ("este mes", "this month"):
        return date(today.year, today.month, 1).isoformat()
    if expr in ("este año", "este anio", "this year"):
        return date(today.year, 1, 1).isoformat()

    # "mes pasado" / "año pasado"
    if expr in ("mes pasado", "last month"):
        first_this = date(today.year, today.month, 1)
        last_month_end = first_this - timedelta(days=1)
        return date(last_month_end.year, last_month_end.month, 1).isoformat()
    if expr in ("año pasado", "anio pasado", "last year"):
        return date(today.year - 1, 1, 1).isoformat()

    # "últimos N días / semanas / meses"
    m = re.match(r"[uú]ltimos?\s+(\d+)\s+(d[ií]as?|semanas?|meses?)", expr)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if "d" in unit:
            return (today - timedelta(days=n)).isoformat()
        if "semana" in unit:
            return (today - timedelta(weeks=n)).isoformat()
        if "mes" in unit:
            # approximate: 30 days per month
            return (today - timedelta(days=n * 30)).isoformat()

    # "last N days/weeks/months" (English fallback)
    m = re.match(r"last\s+(\d+)\s+(days?|weeks?|months?)", expr)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if "day" in unit:
            return (today - timedelta(days=n)).isoformat()
        if "week" in unit:
            return (today - timedelta(weeks=n)).isoformat()
        if "month" in unit:
            return (today - timedelta(days=n * 30)).isoformat()

    # Named month + year: "enero 2025", "march 2024"
    MONTHS_ES = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    MONTHS_EN = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    for months in (MONTHS_ES, MONTHS_EN):
        for name, num in months.items():
            if name in expr:
                year_m = re.search(r"\d{4}", expr)
                year = int(year_m.group()) if year_m else today.year
                return date(year, num, 1).isoformat()

    # "2025" alone → January 1st of that year
    m = re.match(r"^(\d{4})$", expr)
    if m:
        return date(int(m.group(1)), 1, 1).isoformat()

    # Fallback: return as-is and let BigQuery complain if invalid
    return expr


def get_month_end(iso_date: str) -> str:
    """Given a YYYY-MM-DD string (assumed first of month), return last day of that month."""
    import calendar
    d = date.fromisoformat(iso_date)
    last_day = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last_day).isoformat()
