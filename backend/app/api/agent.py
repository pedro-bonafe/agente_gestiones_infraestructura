import time
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.agent.executor import run_agent
from app.config import settings
from app.schemas.agent import AgentRequest, AgentResponse, ConversationContext
from app.services.audit_service import log_interaction
from app.services.memory_service import get_context, update_context_from_result

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)
limiter = Limiter(key_func=get_remote_address)


def verify_api_key(key: str = Security(api_key_header)) -> str:
    if key != settings.api_secret_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


@router.post("/query", response_model=AgentResponse)
@limiter.limit("30/minute")
async def query_agent(
    request: Request,
    body: AgentRequest,
    _: str = Depends(verify_api_key),
) -> AgentResponse:
    # Ensure conversation_id exists
    if not body.conversation_id:
        body = body.model_copy(update={"conversation_id": str(uuid.uuid4())})

    context: ConversationContext = await get_context(body.conversation_id)

    start = time.perf_counter()
    error: str | None = None

    try:
        result = await run_agent(body, context)
    except Exception as exc:
        logger.exception("Agent execution failed", error=str(exc))
        error = str(exc)
        from app.schemas.agent import AgentResult, Intent
        result = AgentResult(
            answer="Ocurrió un error inesperado al procesar la consulta.",
            intent=Intent.UNKNOWN.value,
            entities={},
            tools_used=[],
            confidence=0.0,
        )

    latency_ms = (time.perf_counter() - start) * 1000

    await update_context_from_result(
        context=context,
        intent=result.intent,
        entities=result.entities,
        answer=result.answer,
    )
    await log_interaction(request=body, result=result, latency_ms=latency_ms, error=error)

    logger.info(
        "Agent query completed",
        conversation_id=body.conversation_id,
        intent=result.intent,
        tools=result.tools_used,
        latency_ms=round(latency_ms, 1),
    )

    return AgentResponse(
        answer=result.answer,
        intent=result.intent,
        entities=result.entities,
        tools_used=result.tools_used,
        confidence=result.confidence,
    )
