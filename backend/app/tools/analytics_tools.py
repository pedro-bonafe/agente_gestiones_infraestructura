"""
Analytics tools: atomic async functions that query BigQuery.
Each function maps 1:1 to a tool the agent can call.
"""

from app.schemas.agent import ToolResult
from app.services.bigquery_service import run_named_query

DEFAULT_LISTING_LIMIT = 20
DEFAULT_RANKING_LIMIT = 10


async def get_territory_metrics(departamento: str, localidad: str | None = None) -> ToolResult:
    """
    Returns total, open, finalized, and urgent gestión counts for a territory.
    Provide localidad for locality-level metrics; omit for department-level.
    """
    if localidad:
        return await run_named_query(
            "metrics_by_locality",
            {"departamento": departamento, "localidad": localidad},
        )
    return await run_named_query("metrics_by_department", {"departamento": departamento})


async def get_open_and_delay_metrics(departamento: str, localidad: str | None = None) -> ToolResult:
    """
    Returns open count and average delay metrics (days open, days to resolution).
    Provide localidad for locality-level; omit for department-level.
    """
    if localidad:
        return await run_named_query(
            "open_and_delay_by_locality",
            {"departamento": departamento, "localidad": localidad},
        )
    return await run_named_query("open_and_delay_by_department", {"departamento": departamento})


async def get_ministry_rankings(departamento: str) -> ToolResult:
    """
    Returns ministry ranking by volume, urgency, and average resolution delay
    for a given department.
    """
    return await run_named_query("ministry_rankings_by_department", {"departamento": departamento})


async def get_gestiones_listing(
    departamento: str,
    localidad: str | None = None,
    limit: int = DEFAULT_LISTING_LIMIT,
) -> ToolResult:
    """
    Returns a listing of gestiones ordered by fecha_ingreso ASC (oldest first).
    Provide localidad to filter to locality level.
    """
    capped_limit = min(max(1, limit), 100)
    if localidad:
        return await run_named_query(
            "listing_by_locality",
            {"departamento": departamento, "localidad": localidad, "limit": capped_limit},
        )
    return await run_named_query(
        "listing_by_department",
        {"departamento": departamento, "limit": capped_limit},
    )


async def get_ministry_listing(
    ministerio_agencia_id: str,
    departamento: str | None = None,
    localidad: str | None = None,
    limit: int = DEFAULT_LISTING_LIMIT,
) -> ToolResult:
    """
    Returns gestiones filtered by ministry, optionally within a territory.
    """
    capped_limit = min(max(1, limit), 100)
    if localidad and departamento:
        return await run_named_query(
            "listing_by_ministry_locality",
            {
                "ministerio_agencia_id": ministerio_agencia_id,
                "departamento": departamento,
                "localidad": localidad,
                "limit": capped_limit,
            },
        )
    if departamento:
        return await run_named_query(
            "listing_by_ministry_department",
            {
                "ministerio_agencia_id": ministerio_agencia_id,
                "departamento": departamento,
                "limit": capped_limit,
            },
        )
    # No territory filter — list globally (limited)
    return await run_named_query(
        "listing_by_ministry_global",
        {
            "ministerio_agencia_id": ministerio_agencia_id,
            "limit": capped_limit,
        },
    )


async def get_ministry_metrics(
    ministerio_agencia_id: str,
    departamento: str | None = None,
    localidad: str | None = None,
) -> ToolResult:
    """
    Returns total, open, finalized, and urgent gestión counts for a specific ministry,
    optionally filtered by territory. Use this for count/summary questions about a ministry.
    """
    if localidad and departamento:
        return await run_named_query(
            "metrics_by_ministry_locality",
            {
                "ministerio_agencia_id": ministerio_agencia_id,
                "departamento": departamento,
                "localidad": localidad,
            },
        )
    if departamento:
        return await run_named_query(
            "metrics_by_ministry_department",
            {
                "ministerio_agencia_id": ministerio_agencia_id,
                "departamento": departamento,
            },
        )
    return await run_named_query(
        "metrics_by_ministry_global",
        {"ministerio_agencia_id": ministerio_agencia_id},
    )


async def get_urgent_share_by_ministry(
    ministerio_agencia_id: str,
    departamento: str,
    localidad: str | None = None,
) -> ToolResult:
    """
    Returns total gestiones and urgent count/percentage for a ministry in a territory.
    """
    if localidad:
        return await run_named_query(
            "urgent_share_by_ministry_locality",
            {
                "ministerio_agencia_id": ministerio_agencia_id,
                "departamento": departamento,
                "localidad": localidad,
            },
        )
    return await run_named_query(
        "urgent_share_by_ministry_department",
        {"ministerio_agencia_id": ministerio_agencia_id, "departamento": departamento},
    )


async def get_ranking_localities(limit: int = DEFAULT_RANKING_LIMIT) -> ToolResult:
    """Returns localities ranked by total gestión count (descending)."""
    return await run_named_query("ranking_localities", {"limit": min(max(1, limit), 50)})


async def get_ranking_departments(limit: int = DEFAULT_RANKING_LIMIT) -> ToolResult:
    """Returns departments ranked by total gestión count (descending)."""
    return await run_named_query("ranking_departments", {"limit": min(max(1, limit), 50)})


async def get_ranking_ministries(
    departamento: str | None = None,
    limit: int = DEFAULT_RANKING_LIMIT,
) -> ToolResult:
    """
    Returns ministries ranked by total gestión count.
    Optionally filtered by department.
    """
    capped = min(max(1, limit), 50)
    if departamento:
        return await run_named_query(
            "ranking_ministries_by_department",
            {"departamento": departamento, "limit": capped},
        )
    return await run_named_query("ranking_ministries_global", {"limit": capped})


async def get_ranking_urgent_localities(limit: int = DEFAULT_RANKING_LIMIT) -> ToolResult:
    """Returns localities ranked by urgent gestión count (descending)."""
    return await run_named_query("ranking_urgent_localities", {"limit": min(max(1, limit), 50)})


async def get_general_summary() -> ToolResult:
    """Returns a global summary across all territories and ministries."""
    return await run_named_query("general_summary", {})
