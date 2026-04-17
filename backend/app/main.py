import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.api.agent import limiter, router as agent_router
from app.api.health import router as health_router
from app.api.telegram import router as telegram_router
from app.config import settings


def _configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    logger = structlog.get_logger(__name__)

    logger.info(
        "Starting Infrastructure Management Agent V1",
        llm_provider=settings.llm_provider,
        active_model=settings.active_model_name,
        provider_key_configured=settings.active_provider_key_configured,
        telegram=settings.has_telegram,
        bq_dataset=settings.bq_dataset,
    )

    # Validate provider on startup — fail fast with a clear message
    if not settings.active_provider_key_configured:
        logger.warning(
            "LLM provider API key not configured — agent will return UNKNOWN for all queries",
            provider=settings.llm_provider,
        )
    else:
        try:
            from app.agent.llm_provider import get_provider
            get_provider()  # eagerly initialize + validate
            logger.info("LLM provider initialized", provider=settings.llm_provider)
        except Exception as exc:
            logger.error("LLM provider initialization failed", error=str(exc))

    # Pre-warm catalog cache
    try:
        from app.services.catalog_service import get_catalog
        catalog = get_catalog()
        logger.info(
            "Catalog loaded",
            source=catalog.get("source"),
            departments=len(catalog.get("departments", [])),
            localities=len(catalog.get("localities", [])),
            ministries=len(catalog.get("ministry_map", {})),
        )
    except Exception as exc:
        logger.warning("Catalog pre-warm failed", error=str(exc))

    yield

    logger.info("Shutting down")


app = FastAPI(
    title="Infrastructure Management Agent V2",
    description=(
        "Conversational agent for querying infrastructure management data "
        "using natural language. Powered by OpenAI + BigQuery."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(agent_router)
app.include_router(telegram_router)
app.include_router(health_router)
