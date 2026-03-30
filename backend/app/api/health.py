import structlog
from fastapi import APIRouter

from app.config import settings
from app.services.bigquery_service import check_connectivity as bq_check
from app.services.memory_service import check_connectivity as redis_check

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    bq_ok = await bq_check()
    redis_ok = await redis_check()
    openai_ok = settings.has_openai

    checks = {
        "bigquery": bq_ok,
        "redis": redis_ok,
        "openai": openai_ok,
    }
    all_ok = all(checks.values())
    status = "ok" if all_ok else "degraded"

    logger.info("Health check", status=status, checks=checks)
    return {"status": status, "checks": checks}
