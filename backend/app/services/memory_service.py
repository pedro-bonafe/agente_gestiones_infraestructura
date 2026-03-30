"""
Conversation memory using Redis.
TTL: 24 hours per conversation.
Gracefully degrades to no-memory if Redis is unavailable.
"""

import structlog

from app.config import settings
from app.schemas.agent import ConversationContext, Intent

logger = structlog.get_logger(__name__)

_REDIS_CLIENT = None
_REDIS_AVAILABLE = True
TTL_SECONDS = 86400  # 24 hours
KEY_PREFIX = "agent:conv:"


def _get_redis():
    global _REDIS_CLIENT, _REDIS_AVAILABLE
    if not _REDIS_AVAILABLE:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    try:
        import redis.asyncio as aioredis
        _REDIS_CLIENT = aioredis.from_url(settings.redis_url, decode_responses=True)
        return _REDIS_CLIENT
    except Exception as exc:
        logger.warning("Redis client init failed, memory disabled", error=str(exc))
        _REDIS_AVAILABLE = False
        return None


def _make_key(conversation_id: str) -> str:
    return f"{KEY_PREFIX}{conversation_id}"


async def get_context(conversation_id: str) -> ConversationContext:
    """Load conversation context from Redis. Returns empty context if not found."""
    client = _get_redis()
    if client is None:
        return ConversationContext(conversation_id=conversation_id)
    try:
        raw = await client.get(_make_key(conversation_id))
        if raw is None:
            return ConversationContext(conversation_id=conversation_id)
        return ConversationContext.model_validate_json(raw)
    except Exception as exc:
        logger.warning("Failed to load conversation context", conversation_id=conversation_id, error=str(exc))
        return ConversationContext(conversation_id=conversation_id)


async def save_context(context: ConversationContext) -> None:
    """Save conversation context to Redis with TTL. Silently fails if Redis unavailable."""
    client = _get_redis()
    if client is None:
        return
    try:
        await client.set(
            _make_key(context.conversation_id),
            context.model_dump_json(),
            ex=TTL_SECONDS,
        )
    except Exception as exc:
        logger.error("Failed to save conversation context", conversation_id=context.conversation_id, error=str(exc), error_type=type(exc).__name__)


async def clear_context(conversation_id: str) -> None:
    """Delete conversation context from Redis."""
    client = _get_redis()
    if client is None:
        return
    try:
        await client.delete(_make_key(conversation_id))
        logger.info("Conversation context cleared", conversation_id=conversation_id)
    except Exception as exc:
        logger.warning("Failed to clear context", conversation_id=conversation_id, error=str(exc))


async def update_context_from_result(
    *,
    context: ConversationContext,
    intent: str,
    entities: dict,
    answer: str,
) -> ConversationContext:
    """Build updated context from the agent result and persist it."""
    try:
        intent_enum = Intent(intent)
    except ValueError:
        intent_enum = None

    updated = ConversationContext(
        conversation_id=context.conversation_id,
        last_intent=intent_enum,
        last_entities=entities,
        last_answer=answer[:500] if answer else None,
        turn_count=context.turn_count + 1,
    )
    await save_context(updated)
    return updated


async def check_connectivity() -> bool:
    """Ping Redis. Returns True if reachable."""
    client = _get_redis()
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception as exc:
        logger.warning("Redis ping failed", error=str(exc))
        return False
