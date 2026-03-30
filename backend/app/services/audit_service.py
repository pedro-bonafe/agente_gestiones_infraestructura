"""
Interaction audit logging.
Writes to a local JSONL file. BigQuery write is optional and fire-and-forget.
"""

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import structlog

from app.schemas.agent import AgentRequest, AgentResult

logger = structlog.get_logger(__name__)

INTERACTIONS_PATH = Path(__file__).parent.parent.parent / "data" / "agent_interactions.jsonl"
_file_lock = threading.Lock()


def _write_jsonl_sync(record: dict) -> None:
    try:
        INTERACTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock:
            with open(INTERACTIONS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.error("Failed to write interaction log", error=str(exc))


async def log_interaction(
    *,
    request: AgentRequest,
    result: AgentResult,
    latency_ms: float,
    error: str | None = None,
) -> None:
    """Append a structured interaction record to the JSONL log."""
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "conversation_id": request.conversation_id,
        "user_id": request.user_id,
        "channel": request.channel,
        "message": request.message,
        "intent": result.intent,
        "entities": result.entities,
        "tools_used": result.tools_used,
        "latency_ms": round(latency_ms, 1),
        "answer_length": len(result.answer),
        "confidence": result.confidence,
        "needs_clarification": result.needs_clarification,
        "error": error,
    }
    await asyncio.to_thread(_write_jsonl_sync, record)
