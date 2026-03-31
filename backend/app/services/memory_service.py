"""
Conversation memory using Redis.
TTL: 24 hours per conversation.
Stores up to MAX_TURNS turns per conversation.
Gracefully degrades to no-memory if Redis is unavailable.
"""

import structlog
from datetime import datetime

from app.config import settings
from app.schemas.agent import ConversationContext, Intent, Turn

logger = structlog.get_logger(__name__)

_REDIS_CLIENT = None
_REDIS_AVAILABLE = True
_REDIS_INITIALIZED = False  # True after first successful connection test
TTL_SECONDS = 86400  # 24 hours
KEY_PREFIX = "agent:conv:"
MAX_TURNS = 5


async def _init_redis() -> None:
    """Test real Redis connectivity; fall back to fakeredis if unavailable."""
    global _REDIS_CLIENT, _REDIS_AVAILABLE, _REDIS_INITIALIZED
    if _REDIS_INITIALIZED:
        return
    _REDIS_INITIALIZED = True

    import redis.asyncio as aioredis
    real_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await real_client.ping()
        _REDIS_CLIENT = real_client
        logger.info("Redis connected")
        return
    except Exception as exc:
        logger.warning("Real Redis unavailable, trying fakeredis fallback", error=str(exc))

    try:
        import fakeredis.aioredis as _fakeredis
        _REDIS_CLIENT = _fakeredis.FakeRedis(decode_responses=True)
        logger.warning("Using in-process fakeredis (dev/test only — no persistence across restarts)")
        return
    except ImportError:
        pass

    logger.warning("Memory disabled — configure Redis or install fakeredis for local dev")
    _REDIS_AVAILABLE = False


def _get_redis():
    global _REDIS_CLIENT, _REDIS_AVAILABLE
    if not _REDIS_AVAILABLE:
        return None
    return _REDIS_CLIENT  # May be None before first request completes _init_redis


def _make_key(conversation_id: str) -> str:
    return f"{KEY_PREFIX}{conversation_id}"


async def get_context(conversation_id: str) -> ConversationContext:
    """Load conversation context from Redis. Returns empty context if not found."""
    await _init_redis()
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
    await _init_redis()
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
        logger.error(
            "Failed to save conversation context",
            conversation_id=context.conversation_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def clear_context(conversation_id: str) -> None:
    """Delete conversation context from Redis."""
    await _init_redis()
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
    message: str = "",
) -> ConversationContext:
    """Add a new turn to context and persist it. Keeps only the last MAX_TURNS turns."""
    new_turn = Turn(
        message=message,
        intent=intent,
        entities=entities,
        answer=answer[:500] if answer else "",
        timestamp=datetime.utcnow(),
    )

    # Keep last MAX_TURNS turns
    updated_turns = list(context.turns) + [new_turn]
    updated_turns = updated_turns[-MAX_TURNS:]

    updated = ConversationContext(
        conversation_id=context.conversation_id,
        turns=updated_turns,
        turn_count=context.turn_count + 1,
    )
    await save_context(updated)
    return updated


async def check_connectivity() -> bool:
    """Ping Redis. Returns True if reachable (real or fakeredis)."""
    await _init_redis()
    client = _get_redis()
    if client is None:
        return False
    try:
        await client.ping()
        return True
    except Exception as exc:
        logger.warning("Redis ping failed", error=str(exc))
        return False
