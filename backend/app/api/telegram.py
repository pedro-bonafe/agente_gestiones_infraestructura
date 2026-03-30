import asyncio
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request

from app.agent.executor import run_agent
from app.config import settings
from app.schemas.agent import AgentRequest, ConversationContext
from app.services.memory_service import clear_context, get_context, update_context_from_result
from app.services.telegram_service import (
    extract_message_context,
    is_reset_command,
    send_message,
    send_typing_action,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/telegram", tags=["telegram"])

_TYPING_INTERVAL = 4.0  # seconds between typing refreshes


async def _keep_typing(chat_id: int | str, stop_event: asyncio.Event) -> None:
    """Re-send typing action every _TYPING_INTERVAL seconds until stop_event is set."""
    while not stop_event.is_set():
        await send_typing_action(chat_id)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_TYPING_INTERVAL)
        except asyncio.TimeoutError:
            pass


@router.post("/webhook")
async def telegram_webhook(request: Request) -> dict:
    # Verify webhook secret token
    if settings.telegram_webhook_secret:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != settings.telegram_webhook_secret:
            logger.warning("Telegram webhook: invalid secret token")
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    ctx = extract_message_context(body)
    if ctx is None:
        # Not a text message update — acknowledge silently
        return {"ok": True}

    chat_id = ctx["chat_id"]
    text = ctx["text"]
    user_id = ctx["user_id"]
    conversation_id = ctx["conversation_id"]
    message_id = ctx.get("message_id")

    logger.info(
        "Telegram message received",
        conversation_id=conversation_id,
        user_id=user_id,
        text_len=len(text),
    )

    # Handle reset command
    if is_reset_command(text):
        await clear_context(conversation_id)
        await send_message(chat_id, "Contexto borrado. Podés empezar una nueva consulta.", message_id)
        return {"ok": True}

    # Start typing loop
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(chat_id, stop_typing))

    agent_request = AgentRequest(
        message=text,
        user_id=user_id,
        conversation_id=conversation_id,
        channel="telegram",
    )
    context: ConversationContext = await get_context(conversation_id)

    try:
        result = await run_agent(agent_request, context)
    except Exception as exc:
        logger.exception("Agent failed for Telegram message", error=str(exc))
        stop_typing.set()
        await typing_task
        await send_message(chat_id, "Ocurrió un error al procesar tu consulta. Intentá de nuevo.", message_id)
        return {"ok": True}
    finally:
        stop_typing.set()

    await typing_task

    await update_context_from_result(
        context=context,
        intent=result.intent,
        entities=result.entities,
        answer=result.answer,
    )

    # Build keyboard if clarification needed
    keyboard_options = None
    if result.needs_clarification:
        catalog_deps = __import__(
            "app.services.catalog_service", fromlist=["get_catalog"]
        ).get_catalog().get("departments", [])
        if catalog_deps:
            keyboard_options = catalog_deps[:8]

    await send_message(chat_id, result.answer, message_id, keyboard_options)
    return {"ok": True}
