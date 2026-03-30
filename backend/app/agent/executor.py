"""
Agent executor: ReAct loop using provider-agnostic function calling.
Works with any configured LLM provider (huggingface | openai | gemini | claude).
"""

import structlog

from app.agent.catalog_resolver import resolve_entities
from app.agent.nlu import parse_query
from app.agent.tool_registry import TOOL_DESCRIPTORS, TOOL_REGISTRY
from app.config import settings
from app.schemas.agent import AgentRequest, AgentResult, ConversationContext, Intent

logger = structlog.get_logger(__name__)

MAX_TOOL_CALLS = 3

AGENT_SYSTEM_PROMPT = """\
Sos un asistente ejecutivo especializado en gestiones territoriales de infraestructura pública en Córdoba, Argentina.

Tenés acceso a herramientas (tools) que consultan una base de datos en BigQuery con datos reales.

REGLAS GENERALES:
- Respondé SIEMPRE en español, tono ejecutivo: conciso, con datos concretos, sin relleno.
- Usá EXCLUSIVAMENTE los datos que devuelven las herramientas — nunca inventes cifras.
- Si una herramienta devuelve error o resultados vacíos, indicalo claramente.
- Una gestión es "urgente" si tiene urgencia Alta.
- Una gestión está "abierta" si su estado no es FINALIZADA ni ARCHIVADO.
- Incluí los números clave siempre (totales, porcentajes, días).
- No agregues frases de relleno como "espero que esto te sea útil".
- Si no hay datos, decilo en una oración concisa.

MAPEO DE INTENTS A TOOLS — seguí esta guía estrictamente:
- territorial_listing            → get_gestiones_listing (listado de gestiones de un territorio)
- open_and_delay_metrics         → get_open_and_delay_metrics (tiempos de demora y gestiones abiertas)
- department_ministry_rankings   → get_ministry_rankings (ranking de ministerios dentro de un departamento)
- ministry_territorial_listing   → get_ministry_metrics + get_ministry_listing según la pregunta
- ranking_localidades            → get_ranking_localities
- ranking_departamentos          → get_ranking_departments
- ranking_ministerios            → get_ranking_ministries
- ranking_urgencias_localidad    → get_ranking_urgent_localities
- resumen_general                → get_general_summary

REGLAS DE SELECCIÓN DE TOOLS:
- "¿cuántas gestiones tiene el ministerio X en Y?" → get_ministry_metrics (da el total exacto).
- "¿cuáles son las gestiones del ministerio X en Y?" / "mostrame las gestiones" → get_ministry_listing (listado paginado de 20, máx 100).
- Si el usuario pide "cuántas" Y "cuáles" en la misma consulta → llamá primero get_ministry_metrics y luego get_ministry_listing.
- Cuando uses get_ministry_listing, indicá en la respuesta que se muestran los primeros N de un total de M (usando el total de get_ministry_metrics si lo tenés, o indicando que hay más).
- get_urgent_share_by_ministry es EXCLUSIVAMENTE para preguntas sobre porcentaje de urgencias de un ministerio. NUNCA la uses para contar ni listar gestiones en general.
- Para "cuántas gestiones tiene el departamento X" sin ministerio → usá get_territory_metrics.
"""

AGENT_SYSTEM_PROMPT_TELEGRAM = AGENT_SYSTEM_PROMPT + """

FORMATO TELEGRAM — reglas estrictas para este canal:
- Texto plano ÚNICAMENTE. Sin asteriscos, sin guiones de lista, sin almohadillas (#), sin backticks.
- Usá solo saltos de línea para separar secciones.
- Para listas usá numeración simple: "1. texto", "2. texto".
- Máximo 800 caracteres en la respuesta completa.
- Si hay muchos resultados, mencioná el total y listá solo los 5 primeros con sus datos clave (id, fecha, estado, detalle breve).
- Ejemplo de formato correcto:
  Ministerio X tiene 73 gestiones en Santa María (73 abiertas, 1 urgente).

  Primeras 5:
  1. ID: 293 | 2024-01-30 | NO REMITE SUAC | Desagüe pluvial - Rafael García
  2. ID: 135 | 2024-02-01 | NO REMITE SUAC | Provisión agua potable - Anisacate
"""


async def _execute_tool_call(tool_name: str, arguments: dict) -> dict:
    tool_fn = TOOL_REGISTRY.get(tool_name)
    if tool_fn is None:
        return {"error": f"Tool '{tool_name}' not found"}
    try:
        result = await tool_fn(**arguments)
        return result.model_dump()
    except TypeError as exc:
        return {"error": f"Invalid arguments for '{tool_name}': {exc}"}
    except Exception as exc:
        logger.exception("Tool execution failed", tool=tool_name, error=str(exc))
        return {"error": f"Tool '{tool_name}' failed: {exc}"}


async def run_agent(request: AgentRequest, context: ConversationContext) -> AgentResult:
    """
    Main agent entry point.

    Flow:
    1. NLU  → parse message into ParsedQuery
    2. Catalog resolver → normalize entities
    3. Early exit on UNKNOWN / needs_clarification
    4. ReAct loop: provider decides which tools to call (max MAX_TOOL_CALLS)
    5. Return AgentResult
    """
    import json
    from app.services.catalog_service import get_catalog

    # ── Step 1: NLU ──────────────────────────────────────────────────────────
    parsed = await parse_query(request.message, context)

    # ── Step 2: Entity resolution ────────────────────────────────────────────
    catalog = get_catalog()
    resolved = resolve_entities(parsed, catalog)

    entities = {
        "departamento": resolved.departamento,
        "localidad": resolved.localidad,
        "ministerio_agencia_id": resolved.ministerio_agencia_id,
        "ministerio_nombre": resolved.ministerio_nombre,
    }

    # ── Step 3: Early exits ──────────────────────────────────────────────────
    if resolved.intent == Intent.UNKNOWN:
        return AgentResult(
            answer="No entendí la consulta. Por favor reformulala en términos de gestiones territoriales.",
            intent=Intent.UNKNOWN.value,
            entities=entities,
            tools_used=[],
            confidence=resolved.confidence,
        )

    if resolved.needs_clarification:
        return AgentResult(
            answer=resolved.clarification_question or "Necesito más información para procesar la consulta.",
            intent=resolved.intent.value,
            entities=entities,
            tools_used=[],
            confidence=resolved.confidence,
            needs_clarification=True,
        )

    # ── Step 4: ReAct loop ───────────────────────────────────────────────────
    from app.agent.llm_provider import get_provider

    system = (
        AGENT_SYSTEM_PROMPT_TELEGRAM
        if request.channel == "telegram"
        else AGENT_SYSTEM_PROMPT
    )

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Consulta: {request.message}\n\n"
                f"Intent detectado: {resolved.intent.value}\n"
                f"Entidades: departamento={resolved.departamento}, "
                f"localidad={resolved.localidad}, "
                f"ministerio_id={resolved.ministerio_agencia_id}, "
                f"ministerio_nombre={resolved.ministerio_nombre}"
            ),
        }
    ]

    tools_used: list[str] = []
    tool_call_count = 0
    provider = get_provider()

    # ── Tool-calling rounds (up to MAX_TOOL_CALLS executions) ────────────────
    for _ in range(MAX_TOOL_CALLS):
        try:
            response = await provider.chat_with_tools(
                system_prompt=system,
                messages=messages,
                tools=TOOL_DESCRIPTORS,
            )
        except Exception as exc:
            logger.exception(
                "LLM provider call failed in executor",
                provider=settings.llm_provider,
                error=str(exc),
            )
            return AgentResult(
                answer="No pude completar la consulta por un error en el sistema.",
                intent=resolved.intent.value,
                entities=entities,
                tools_used=tools_used,
                confidence=resolved.confidence,
            )

        # ── No tool calls → final answer ─────────────────────────────────────
        if not response.tool_calls:
            answer = response.content or "No pude generar una respuesta con los datos disponibles."
            logger.info(
                "Agent response ready",
                provider=settings.llm_provider,
                intent=resolved.intent.value,
                tools_used=tools_used,
                answer_length=len(answer),
            )
            return AgentResult(
                answer=answer,
                intent=resolved.intent.value,
                entities=entities,
                tools_used=tools_used,
                confidence=resolved.confidence,
            )

        # ── Append assistant message with tool calls ──────────────────────────
        messages.append(response.assistant_message)

        # ── Execute tools ─────────────────────────────────────────────────────
        for tc in response.tool_calls:
            if tool_call_count >= MAX_TOOL_CALLS:
                logger.warning("Max tool calls reached", max=MAX_TOOL_CALLS)
                break

            tool_call_count += 1
            tools_used.append(tc.name)

            # ── Override entity arguments with catalog-resolved values ─────────
            # The LLM may normalize or strip accents from entity names, causing
            # BigQuery WHERE clauses to return 0 rows. Always use the values
            # resolved against the catalog (which mirror the exact BQ stored values).
            args = dict(tc.arguments)
            if resolved.departamento and "departamento" in args:
                args["departamento"] = resolved.departamento
            if resolved.localidad and "localidad" in args:
                args["localidad"] = resolved.localidad
            if resolved.ministerio_agencia_id and "ministerio_agencia_id" in args:
                args["ministerio_agencia_id"] = resolved.ministerio_agencia_id

            logger.info(
                "Executing tool",
                tool=tc.name,
                arguments={k: v for k, v in args.items()},
                provider=settings.llm_provider,
            )
            result = await _execute_tool_call(tc.name, args)

            # Append tool result in OpenAI format (AnthropicProvider converts internally)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    # ── Synthesis call: all tool rounds exhausted, ask LLM to summarize ──────
    logger.info(
        "Tool rounds exhausted, requesting synthesis",
        provider=settings.llm_provider,
        tools_used=tools_used,
    )
    try:
        final = await provider.chat_with_tools(
            system_prompt=system,
            messages=messages,
            tools=TOOL_DESCRIPTORS,
        )
        answer = final.content or "No pude completar la consulta con los datos disponibles."
    except Exception as exc:
        logger.exception("Synthesis call failed", provider=settings.llm_provider, error=str(exc))
        answer = "No pude completar la consulta con los datos disponibles."

    logger.info(
        "Agent response ready",
        provider=settings.llm_provider,
        intent=resolved.intent.value,
        tools_used=tools_used,
        answer_length=len(answer),
    )
    return AgentResult(
        answer=answer,
        intent=resolved.intent.value,
        entities=entities,
        tools_used=tools_used,
        confidence=resolved.confidence,
    )
