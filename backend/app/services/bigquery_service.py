import asyncio
import time
import structlog
from typing import Any

from app.config import settings
from app.repositories.query_catalog import QUERY_CATALOG
from app.schemas.agent import ToolResult

logger = structlog.get_logger(__name__)

_BQ_CLIENT = None


def _get_client():
    global _BQ_CLIENT
    if _BQ_CLIENT is not None:
        return _BQ_CLIENT
    try:
        from pathlib import Path
        from google.cloud import bigquery

        kwargs: dict = {"project": settings.gcp_project_id}

        cred_path = settings.google_application_credentials
        if cred_path:
            path = Path(cred_path)
            if not path.is_absolute():
                # Resolve relative path from the backend root (parent of app/)
                path = Path(__file__).resolve().parent.parent.parent / cred_path.lstrip("./")
            if path.exists():
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(str(path))
                kwargs["credentials"] = creds
            else:
                logger.warning("Credentials file not found, falling back to ADC", path=str(path))

        _BQ_CLIENT = bigquery.Client(**kwargs)
        return _BQ_CLIENT
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize BigQuery client: {exc}") from exc


def _build_query_params(params: dict[str, Any]):
    from google.cloud import bigquery

    bq_params = []
    for key, value in params.items():
        if isinstance(value, list):
            bq_params.append(
                bigquery.ArrayQueryParameter(key, "STRING", [str(v) for v in value])
            )
        elif isinstance(value, int):
            bq_params.append(bigquery.ScalarQueryParameter(key, "INT64", value))
        elif isinstance(value, float):
            bq_params.append(bigquery.ScalarQueryParameter(key, "FLOAT64", value))
        elif value is None:
            bq_params.append(bigquery.ScalarQueryParameter(key, "STRING", None))
        else:
            bq_params.append(bigquery.ScalarQueryParameter(key, "STRING", str(value)))
    return bq_params


def _run_query_sync(sql: str, params: dict[str, Any], tool_name: str) -> ToolResult:
    from google.api_core.exceptions import DeadlineExceeded, ServiceUnavailable, TooManyRequests
    import google.auth.exceptions

    max_retries = 3
    last_error: Exception | None = None

    for attempt in range(max_retries):
        start = time.perf_counter()
        try:
            client = _get_client()
            from google.cloud import bigquery

            job_config = bigquery.QueryJobConfig(
                query_parameters=_build_query_params(params),
            )
            query_job = client.query(sql, job_config=job_config)
            rows = list(query_job.result(timeout=30))
            elapsed_ms = (time.perf_counter() - start) * 1000

            result_rows = [dict(row) for row in rows]
            return ToolResult(
                tool_name=tool_name,
                rows=result_rows,
                row_count=len(result_rows),
                query_ms=round(elapsed_ms, 1),
            )

        except (DeadlineExceeded, ServiceUnavailable, TooManyRequests) as exc:
            last_error = exc
            wait = 0.5 * (2**attempt)
            logger.warning(
                "BigQuery transient error, retrying",
                tool=tool_name,
                attempt=attempt + 1,
                wait_seconds=wait,
                error=str(exc),
            )
            time.sleep(wait)

        except google.auth.exceptions.DefaultCredentialsError as exc:
            logger.error("BigQuery credentials not configured", error=str(exc))
            return ToolResult(
                tool_name=tool_name,
                rows=[],
                row_count=0,
                error="BigQuery credentials not configured",
            )

        except Exception as exc:
            logger.exception("BigQuery unexpected error", tool=tool_name, error=str(exc))
            return ToolResult(
                tool_name=tool_name,
                rows=[],
                row_count=0,
                error=f"Unexpected error: {exc}",
            )

    return ToolResult(
        tool_name=tool_name,
        rows=[],
        row_count=0,
        error=f"BigQuery failed after {max_retries} retries: {last_error}",
    )


async def run_named_query(query_name: str, params: dict[str, Any]) -> ToolResult:
    """Execute a predefined query from the catalog."""
    if query_name not in QUERY_CATALOG:
        return ToolResult(
            tool_name=query_name,
            rows=[],
            row_count=0,
            error=f"Query '{query_name}' not found in catalog",
        )

    sql = QUERY_CATALOG[query_name].format(dataset=settings.bq_dataset)
    logger.info("Executing named query", query=query_name, params=list(params.keys()))
    return await asyncio.to_thread(_run_query_sync, sql, params, query_name)


async def run_raw_query(sql: str, params: dict[str, Any], tool_name: str = "raw_query") -> ToolResult:
    """Execute a raw SQL string (used for catalog loading)."""
    formatted_sql = sql.format(dataset=settings.bq_dataset)
    logger.info("Executing raw query", tool=tool_name)
    return await asyncio.to_thread(_run_query_sync, formatted_sql, params, tool_name)


async def check_connectivity() -> bool:
    """Ping BigQuery with a trivial query. Returns True if reachable."""
    try:
        result = await run_raw_query(
            "SELECT 1 AS ping",
            {},
            tool_name="health_check",
        )
        return result.error is None
    except Exception:
        return False
