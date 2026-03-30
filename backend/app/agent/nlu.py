"""
NLU layer: parses natural language queries into a typed ParsedQuery.
Uses the configured LLM provider (huggingface | openai | gemini | claude).
No heuristics — if the provider can't parse, returns Intent.UNKNOWN.
"""

import structlog

from app.config import settings
from app.schemas.agent import ConversationContext, Intent, ParsedQuery

logger = structlog.get_logger(__name__)

NLU_SYSTEM_PROMPT = """\
Sos un parser semántico para un sistema de gestiones territoriales de infraestructura pública en Córdoba, Argentina.

Tu ÚNICA tarea es extraer estructura de lenguaje natural. No respondas preguntas ni des información. Solo extraé estructura.

INTENTS disponibles — devolvé EXACTAMENTE uno:
- territorial_listing        : listar o ver gestiones de un departamento o localidad
- open_and_delay_metrics     : métricas de demora, tiempo promedio, gestiones abiertas
- department_ministry_rankings: qué ministerio tiene más gestiones / más demora / más urgentes en un departamento
- ministry_territorial_listing: gestiones de un ministerio específico en un territorio
- ranking_localidades        : ranking de localidades por cantidad de gestiones
- ranking_departamentos      : ranking de departamentos por cantidad de gestiones
- ranking_ministerios        : ranking de ministerios por cantidad (global o por departamento)
- ranking_urgencias_localidad: ranking de localidades por gestiones urgentes
- resumen_general            : resumen global sin filtro territorial
- unknown                    : la consulta no es sobre gestiones territoriales o no se entiende

REGLAS:
- Extraé los valores RAW tal como aparecen en el mensaje (sin normalizar) — el sistema los resuelve contra el catálogo.
- confidence: 0.85-1.0 si estás seguro; 0.50-0.75 si hay ambigüedad.
- Si el intent requiere territorio (territorial_listing, open_and_delay_metrics, department_ministry_rankings, ministry_territorial_listing) y no identificás territorio → needs_clarification=true.
- Para followups (mensajes cortos con "y", "también", "en ese", etc.) → completá entidades faltantes con el contexto.
- "urgentes de un ministerio" → ministry_territorial_listing.
- "ministerio que más demora" → department_ministry_rankings.
"""

NLU_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "intent": {
            "type": "string",
            "enum": [i.value for i in Intent],
        },
        "departamento": {"type": ["string", "null"]},
        "localidad": {"type": ["string", "null"]},
        "ministerio_nombre": {"type": ["string", "null"]},
        "ministerio_agencia_id": {"type": ["string", "null"]},
        "cantidad": {"type": ["integer", "null"]},
        "orden_campo": {"type": ["string", "null"]},
        "orden_direccion": {"type": ["string", "null"]},
        "urgencia": {"type": ["string", "null"]},
        "estado": {"type": ["string", "null"]},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": [
        "intent", "departamento", "localidad", "ministerio_nombre",
        "ministerio_agencia_id", "cantidad", "orden_campo", "orden_direccion",
        "urgencia", "estado", "needs_clarification", "clarification_question",
        "confidence",
    ],
}


def _build_user_prompt(message: str, context: ConversationContext) -> str:
    lines = []
    if context.last_intent:
        lines.append(f"- Último intent: {context.last_intent}")
    if context.last_entities.get("departamento"):
        lines.append(f"- Último departamento: {context.last_entities['departamento']}")
    if context.last_entities.get("localidad"):
        lines.append(f"- Última localidad: {context.last_entities['localidad']}")
    if context.last_entities.get("ministerio_agencia_id"):
        lines.append(f"- Último ministerio ID: {context.last_entities['ministerio_agencia_id']}")
    if context.last_entities.get("ministerio_nombre"):
        lines.append(f"- Último ministerio: {context.last_entities['ministerio_nombre']}")

    ctx_block = (
        "Contexto conversacional previo (útil para followups):\n" + "\n".join(lines)
        if lines
        else "Sin contexto previo."
    )
    return f"Consulta: {message}\n\n{ctx_block}"


def _coerce_parsed_data(data: dict) -> dict:
    """Validate and sanitize raw data from the LLM before building ParsedQuery."""
    valid_intents = {i.value for i in Intent}
    if data.get("intent") not in valid_intents:
        data["intent"] = Intent.UNKNOWN.value

    try:
        data["confidence"] = max(0.0, min(float(data.get("confidence", 0.5)), 1.0))
    except (TypeError, ValueError):
        data["confidence"] = 0.5

    if data.get("orden_campo") not in {None, "fecha"}:
        data["orden_campo"] = "fecha" if data.get("orden_direccion") else None
    if data.get("orden_direccion") not in {None, "ASC", "DESC"}:
        data["orden_direccion"] = None

    return data


class NLUError(Exception):
    """Raised when the NLU provider is unavailable or fails."""


async def parse_query(message: str, context: ConversationContext) -> ParsedQuery:
    """
    Parse a natural language message into a typed ParsedQuery.
    Uses the configured LLM provider. On failure returns Intent.UNKNOWN.
    """
    if not settings.active_provider_key_configured:
        logger.warning(
            "LLM provider not configured",
            provider=settings.llm_provider,
        )
        return ParsedQuery(
            intent=Intent.UNKNOWN,
            needs_clarification=True,
            clarification_question=(
                f"El proveedor de LLM '{settings.llm_provider}' no tiene API key configurada."
            ),
            confidence=0.0,
        )

    logger.info(
        "Running NLU",
        provider=settings.llm_provider,
        model=settings.active_model_name,
    )

    try:
        from app.agent.llm_provider import get_provider
        provider = get_provider()
        raw = await provider.structured_parse(
            system_prompt=NLU_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(message, context),
            json_schema=NLU_JSON_SCHEMA,
        )
        data = _coerce_parsed_data(raw)
        result = ParsedQuery(**data)
        logger.info(
            "NLU complete",
            intent=result.intent,
            confidence=result.confidence,
            needs_clarification=result.needs_clarification,
            provider=settings.llm_provider,
        )
        return result

    except Exception as exc:
        logger.error("NLU failed", provider=settings.llm_provider, error=str(exc))
        return ParsedQuery(
            intent=Intent.UNKNOWN,
            needs_clarification=True,
            clarification_question="No pude interpretar la consulta. Por favor reformulala.",
            confidence=0.0,
        )
