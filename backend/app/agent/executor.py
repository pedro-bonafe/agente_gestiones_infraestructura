"""
Agent executor V2: ReAct loop with 3 tools.
Flow: NLU → date resolver → query rewriter → catalog resolver → ReAct → synthesis
"""

import structlog

from app.agent.catalog_resolver import resolve_entities
from app.agent.date_resolver import resolve_dates
from app.agent.nlu import parse_query
from app.agent.query_rewriter import resolve_category, rewrite
from app.agent.tool_registry import TOOL_DESCRIPTORS, TOOL_REGISTRY
from app.config import settings
from app.schemas.agent import AgentRequest, AgentResult, ConversationContext, Intent

logger = structlog.get_logger(__name__)

MAX_TOOL_CALLS = 3

AGENT_SYSTEM_PROMPT = """\
Sos un asistente ejecutivo especializado en gestiones territoriales de infraestructura pública en Córdoba, Argentina.

Tenés acceso a 3 herramientas que consultan BigQuery con datos reales:
- buscar_gestiones: para listar gestiones concretas (cuáles, detalles)
- consultar_estadisticas: para responder preguntas numéricas (cuántas, rankings, promedios) Y para consultar información política de una localidad (intendente, partido, semáforo político, electores)
- buscar_por_proximidad: para gestiones cercanas a una localidad

REGLAS GENERALES:
- Respondé SIEMPRE en español, tono ejecutivo: conciso, con datos concretos.
- Usá EXCLUSIVAMENTE los datos que devuelven las herramientas — nunca inventes cifras.
- Si una herramienta devuelve error o resultados vacíos, indicalo claramente.
- Una gestión es "urgente" si tiene urgencia Alta.
- Una gestión está "abierta" si su estado no es FINALIZADA ni ARCHIVADO.
- Incluí los números clave siempre (totales, porcentajes, días).
- No agregues frases de relleno como "espero que esto te sea útil".

REGLA CRÍTICA — NUNCA preguntes al usuario por aclaraciones. Los filtros ya fueron resueltos por el sistema antes de llegarte. Siempre llamá una herramienta con los filtros disponibles y presentá los resultados directamente.

PAGINACIÓN — cuando el resultado incluye has_more=true:
- Mostrá los resultados recibidos.
- Agregá al final: "Mostrando X de Y gestiones en total. ¿Querés ver todas? Respondé sí."
- NO agregues este mensaje si has_more=false o si ya se pidieron todas (limit >= total_count).

SELECCIÓN DE TOOLS — el intent ya fue clasificado, seguí esta tabla:
- intent=buscar_listado → llamá buscar_gestiones con todos los filtros disponibles
- intent=consultar_numerico → llamá consultar_estadisticas con todos los filtros disponibles
- intent=buscar_por_proximidad → llamá buscar_por_proximidad con lat_ref/lon_ref/radio_km
- intent=consultar_info_politica → llamá consultar_estadisticas con la pregunta original; los campos color_semaforo, intendente_jefe_comunal, partido_politico y electores están en vw_agent_gestiones
- Para preguntas mixtas (cuántas + cuáles): llamá primero consultar_estadisticas, luego buscar_gestiones
"""

AGENT_SYSTEM_PROMPT_TELEGRAM = AGENT_SYSTEM_PROMPT + """
FORMATO TELEGRAM — reglas estrictas para este canal:
- El canal renderiza HTML. Usá **campo:** para nombres de campos (se mostrarán en negrita).
- Sin almohadillas (#), sin backticks, sin guiones como bullet points.
- Para listas de gestiones usá numeración: "1.", "2.", etc.
- Dejá una línea en blanco entre cada gestión del listado.
- Campos a mostrar por gestión: ID, Fecha, Estado, Urgencia, Ministerio, Categoría, Detalle, Localidad, Días abierta.
- Máximo 1200 caracteres en la respuesta completa.
- Si hay muchos resultados, mencioná el total y listá solo los primeros con datos clave.

Ejemplo de formato para una gestión:
1. **ID:** 130_COLÓN_VILLA ALLENDE
**Fecha:** 2023-09-17  **Estado:** NO REMITE SUAC
**Categoría:** Gestión municipal  **Urgencia:** Media
**Detalle:** Centro de Veteranos Malvinas
**Días abierta:** 926
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
    Main agent entry point V2.

    Flow:
    1. NLU  → parse message into ParsedQuery (3 intents)
    2. Date resolver → convert relative dates to absolute ISO strings
    3. Query rewriter → expand search_terms, infer category_hint
    4. Catalog resolver → normalize entities against BQ catalog
    5. Early exit on UNKNOWN / needs_clarification
    6. ReAct loop (max MAX_TOOL_CALLS tool executions)
    7. Synthesis → return AgentResult
    """
    import json
    from app.services.catalog_service import get_catalog

    # ── Pre-NLU: handle short negative replies to pagination offers ───────────
    _NEGATIVE_REPLIES = {"no", "nop", "nope", "no gracias", "no, gracias", "nel", "paso"}
    if request.message.strip().lower() in _NEGATIVE_REPLIES:
        last_intent = context.last_intent
        if last_intent in ("buscar_listado", "buscar_por_proximidad"):
            return AgentResult(
                answer="Entendido. Si necesitás otra consulta, estoy a disposición.",
                intent=last_intent,
                entities=context.last_entities,
                tools_used=[],
                confidence=1.0,
            )

    # ── Step 1: NLU ──────────────────────────────────────────────────────────
    parsed = await parse_query(request.message, context)

    # ── Step 2: Date resolver ─────────────────────────────────────────────────
    if parsed.fecha_desde or parsed.fecha_hasta:
        parsed.fecha_desde, parsed.fecha_hasta = resolve_dates(
            parsed.fecha_desde, parsed.fecha_hasta
        )

    # ── Step 3: Query rewriter ────────────────────────────────────────────────
    if parsed.search_terms:
        rewrite_result = rewrite(parsed.search_terms, parsed.categoria)
        parsed.search_terms = rewrite_result.expanded_terms
        if rewrite_result.category_hint and not parsed.categoria:
            parsed.categoria = rewrite_result.category_hint
    elif parsed.categoria:
        parsed.categoria = resolve_category(parsed.categoria) or parsed.categoria

    # ── Step 4: Entity resolution ─────────────────────────────────────────────
    catalog = get_catalog()
    resolved = resolve_entities(parsed, catalog)

    entities = {
        "departamento": resolved.departamento,
        "localidad": resolved.localidad,
        "ministerio_agencia_id": resolved.ministerio_agencia_id,
        "ministerio_nombre": resolved.ministerio_nombre,
    }

    # ── Step 5: Early exits ───────────────────────────────────────────────────
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

    # ── Step 6: Build initial message with all resolved context ───────────────
    from app.agent.llm_provider import get_provider

    system = (
        AGENT_SYSTEM_PROMPT_TELEGRAM
        if request.channel == "telegram"
        else AGENT_SYSTEM_PROMPT
    )

    # Build context block from resolved entities + filters
    filter_parts = []
    if resolved.departamento:
        filter_parts.append(f"departamento={resolved.departamento}")
    if resolved.localidad:
        filter_parts.append(f"localidad={resolved.localidad}")
    if resolved.ministerio_agencia_id:
        filter_parts.append(f"ministerio_id={resolved.ministerio_agencia_id}")
    if resolved.search_terms:
        filter_parts.append(f"search_terms={resolved.search_terms}")
    if resolved.categoria:
        filter_parts.append(f"categoria={resolved.categoria}")
    if resolved.estado:
        filter_parts.append(f"estado={resolved.estado}")
    if resolved.canal_origen:
        filter_parts.append(f"canal={resolved.canal_origen}")
    if resolved.fecha_desde:
        filter_parts.append(f"desde={resolved.fecha_desde}")
    if resolved.fecha_hasta:
        filter_parts.append(f"hasta={resolved.fecha_hasta}")
    if resolved.radio_km:
        filter_parts.append(f"radio_km={resolved.radio_km}")
    if resolved.geo_lat is not None:
        filter_parts.append(f"lat_ref={resolved.geo_lat}")
    if resolved.geo_lon is not None:
        filter_parts.append(f"lon_ref={resolved.geo_lon}")

    # Map intent to the recommended tool
    _INTENT_TOOL_HINT = {
        "buscar_listado": "buscar_gestiones",
        "consultar_numerico": "consultar_estadisticas",
        "buscar_por_proximidad": "buscar_por_proximidad",
        "consultar_info_politica": "consultar_estadisticas",
    }
    tool_hint = _INTENT_TOOL_HINT.get(resolved.intent.value, "buscar_gestiones")

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Consulta: {request.message}\n\n"
                f"Intent detectado: {resolved.intent.value} → llamá {tool_hint}\n"
                f"Filtros resueltos: {', '.join(filter_parts) or 'ninguno'}\n\n"
                "ACCIÓN REQUERIDA: llamá la herramienta indicada con los filtros de arriba. No preguntes al usuario."
            ),
        }
    ]

    tools_used: list[str] = []
    tool_call_count = 0
    provider = get_provider()

    # ── Tool-calling rounds ───────────────────────────────────────────────────
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

        # No tool calls → final answer
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

        messages.append(response.assistant_message)

        # Execute tools
        for tc in response.tool_calls:
            if tool_call_count >= MAX_TOOL_CALLS:
                logger.warning("Max tool calls reached", max=MAX_TOOL_CALLS)
                break

            tool_call_count += 1
            tools_used.append(tc.name)

            # Override entity arguments with catalog-resolved values
            args = dict(tc.arguments)

            if resolved.departamento and "departamento" in args:
                args["departamento"] = resolved.departamento
            if resolved.localidad and "localidad" in args:
                args["localidad"] = resolved.localidad
            if resolved.ministerio_agencia_id and "ministerio_agencia_id" in args:
                args["ministerio_agencia_id"] = resolved.ministerio_agencia_id

            # For proximity: inject resolved coords from catalog
            if tc.name == "buscar_por_proximidad":
                if resolved.geo_lat is not None and resolved.geo_lon is not None:
                    args["lat_ref"] = resolved.geo_lat
                    args["lon_ref"] = resolved.geo_lon
                if resolved.radio_km and "radio_km" not in args:
                    args["radio_km"] = resolved.radio_km
                # Inject cantidad as limit (user-requested page size)
                if resolved.cantidad and "limit" not in args:
                    args["limit"] = resolved.cantidad

            # For buscar_gestiones: inject search_terms, categoria, estado, dates if not provided
            if tc.name == "buscar_gestiones":
                if resolved.search_terms and "search_terms" not in args:
                    args["search_terms"] = resolved.search_terms
                if resolved.categoria and "categoria" not in args:
                    args["categoria"] = resolved.categoria
                if resolved.estado and "estado" not in args:
                    args["estado"] = resolved.estado
                if resolved.canal_origen and "canal_origen" not in args:
                    args["canal_origen"] = resolved.canal_origen
                if resolved.fecha_desde and "fecha_desde" not in args:
                    args["fecha_desde"] = resolved.fecha_desde
                if resolved.fecha_hasta and "fecha_hasta" not in args:
                    args["fecha_hasta"] = resolved.fecha_hasta
                # Inject cantidad as limit (user-requested page size)
                if resolved.cantidad and "limit" not in args:
                    args["limit"] = resolved.cantidad

            # For consultar_estadisticas: inject all resolved filters as context
            if tc.name == "consultar_estadisticas":
                if resolved.fecha_desde and "fecha_desde" not in args:
                    args["fecha_desde"] = resolved.fecha_desde
                if resolved.fecha_hasta and "fecha_hasta" not in args:
                    args["fecha_hasta"] = resolved.fecha_hasta
                if resolved.categoria and "categoria" not in args:
                    args["categoria"] = resolved.categoria
                if resolved.estado and "estado" not in args:
                    args["estado"] = resolved.estado
                # Inject the original question if not set
                if "pregunta" not in args:
                    args["pregunta"] = request.message

            logger.info(
                "Executing tool",
                tool=tc.name,
                arguments={k: v for k, v in args.items() if k != "search_terms"},
                provider=settings.llm_provider,
            )
            result = await _execute_tool_call(tc.name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    # ── Synthesis call: no tools — force text response ────────────────────────
    logger.info(
        "Tool rounds exhausted, requesting synthesis",
        provider=settings.llm_provider,
        tools_used=tools_used,
    )
    try:
        final = await provider.chat_with_tools(
            system_prompt=system,
            messages=messages,
            tools=[],  # Empty → pure text synthesis
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
