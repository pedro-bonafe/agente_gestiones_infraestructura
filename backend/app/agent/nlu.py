"""
NLU layer: parses natural language queries into a typed ParsedQuery.
Uses the configured LLM provider (huggingface | openai | gemini | claude).
"""

import structlog

from app.config import settings
from app.schemas.agent import ConversationContext, Intent, ParsedQuery

logger = structlog.get_logger(__name__)

NLU_SYSTEM_PROMPT = """\
Sos un parser semántico para un sistema de gestiones territoriales de infraestructura pública en Córdoba, Argentina.

Tu ÚNICA tarea es extraer estructura de lenguaje natural. No respondas preguntas ni des información. Solo extraé estructura.

INTENTS disponibles — devolvé EXACTAMENTE uno:
- buscar_listado        : listar, ver, mostrar gestiones concretas (con sus detalles)
- consultar_numerico    : cuántos, rankings, promedios, porcentajes, estadísticas, demoras, resúmenes
- buscar_por_proximidad : gestiones cerca de / en un radio de una localidad
- unknown               : la consulta no es sobre gestiones territoriales o no se entiende

REGLAS GENERALES:
- Extraé valores RAW tal como aparecen en el mensaje — el sistema los normaliza después.
- confidence: 0.85-1.0 si estás seguro; 0.50-0.75 si hay ambigüedad.
- Para followups (mensajes cortos con "y", "también", "en ese", etc.) → completá con el contexto previo.
- Para afirmativos de paginación ("sí", "si", "ver todas", "todas", "mostrame todas", "quiero verlas todas"):
  si el turno anterior fue buscar_listado → reproducí el mismo intent + mismas entidades + cantidad=100

REGLAS DE INTENT:
- "¿cuáles son las gestiones?" / "mostrame" / "listame" → buscar_listado
- "¿cuántas?" / "¿qué porcentaje?" / "ranking de" / "promedio de días" / "resumen" → consultar_numerico
- "cerca de" / "en un radio de" / "a X km de" → buscar_por_proximidad
- Si hay dudas entre buscar_listado y consultar_numerico: pregunta numérica → consultar_numerico

EXTRACCIÓN DE FILTROS (solo si el usuario los menciona explícitamente):
- search_terms: lista de términos de búsqueda temática (ej: ["pavimento"], ["agua potable", "cisterna"])
- categoria: nombre de categoría (ej: "Infraestructura vial", "Agua y saneamiento", "Educación")
- estado: estado de la gestión (ej: "INGRESADO", "NO REMITE SUAC", "FINALIZADA")
- canal_origen: canal por donde llegó (ej: "WHATSAPP", "MAIL")
- fecha_desde / fecha_hasta: expresiones de fecha (ej: "enero 2025", "este mes", "últimos 90 días", "2024-01-01")
- radio_km: número en kilómetros para búsqueda por proximidad (default 20 si no se especifica)

DEPARTAMENTO vs LOCALIDAD (importante):
- departamento: región administrativa amplia. Ejemplos: Colón, Río Cuarto, Capital, Punilla, Calamuchita, Totoral
- localidad: ciudad o pueblo específico. Ejemplos: La Falda, Villa Carlos Paz, Jesús María, Cosquín
- "en Colón" / "del departamento Colón" → departamento="Colón", localidad=null
- "en La Falda" / "en Villa Carlos Paz" → localidad="La Falda", departamento=null
- Si no estás seguro si es departamento o localidad, preferí departamento para nombres de departamentos conocidos.

ESTADOS VÁLIDOS: ARCHIVADO, DERIVADO A SUAC, FINALIZADA, INGRESADO, LISTA PARA INNAUGURAR, NO REMITE SUAC
CANALES VÁLIDOS: APP, MAIL, WHATSAPP, TELEFONO_FUNCIONARIO, Agenda/reunión, Otro
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
        "search_terms": {
            "type": "array",
            "items": {"type": "string"},
        },
        "categoria": {"type": ["string", "null"]},
        "estado": {"type": ["string", "null"]},
        "canal_origen": {"type": ["string", "null"]},
        "fecha_desde": {"type": ["string", "null"]},
        "fecha_hasta": {"type": ["string", "null"]},
        "radio_km": {"type": ["number", "null"]},
        "cantidad": {"type": ["integer", "null"]},
        "confidence": {"type": "number"},
    },
    "required": [
        "intent", "departamento", "localidad", "ministerio_nombre",
        "ministerio_agencia_id", "search_terms", "categoria", "estado",
        "canal_origen", "fecha_desde", "fecha_hasta", "radio_km", "cantidad",
        "confidence",
    ],
}


def _build_user_prompt(message: str, context: ConversationContext) -> str:
    lines = []
    for turn in context.recent_turns(3):
        lines.append(f"- [{turn.intent}] '{turn.message[:80]}' → entidades: {turn.entities}")

    ctx_block = (
        "Contexto conversacional reciente (útil para followups):\n" + "\n".join(lines)
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

    # Ensure search_terms is a list
    if not isinstance(data.get("search_terms"), list):
        data["search_terms"] = []

    # Clamp radio_km
    if data.get("radio_km") is not None:
        try:
            data["radio_km"] = max(1.0, min(float(data["radio_km"]), 500.0))
        except (TypeError, ValueError):
            data["radio_km"] = 20.0

    # NLU never sets clarification — that's catalog_resolver's job
    data["needs_clarification"] = False
    data["clarification_question"] = None

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
