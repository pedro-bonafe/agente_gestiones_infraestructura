"""
Analytics tools V2: 3 tools that cover all query types.
- buscar_gestiones: hybrid BM25 + exact filters, returns gestión listings
- consultar_estadisticas: text-to-SQL for counts, rankings, averages
- buscar_por_proximidad: Haversine-based geographic search
"""

import structlog

from app.schemas.agent import ToolResult
from app.services.bigquery_service import run_named_query, run_raw_query

logger = structlog.get_logger(__name__)

DEFAULT_LISTING_LIMIT = 5
MAX_LISTING_LIMIT = 100


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1: buscar_gestiones
# ─────────────────────────────────────────────────────────────────────────────

async def buscar_gestiones(
    departamento: str | None = None,
    localidad: str | None = None,
    ministerio_agencia_id: str | None = None,
    search_terms: list[str] | None = None,
    categoria: str | None = None,
    estado: str | None = None,
    canal_origen: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    limit: int = DEFAULT_LISTING_LIMIT,
) -> ToolResult:
    """
    Returns a listing of gestiones with hybrid exact + text search.
    Builds dynamic SQL based on provided filters.
    """
    from app.config import settings
    from google.cloud import bigquery as bq

    capped_limit = min(max(1, limit), MAX_LISTING_LIMIT)
    dataset = settings.bq_dataset

    conditions: list[str] = []
    params: dict = {"limit": capped_limit}

    if departamento:
        conditions.append("departamento = @departamento")
        params["departamento"] = departamento

    if localidad:
        conditions.append("localidad = @localidad")
        params["localidad"] = localidad

    if ministerio_agencia_id:
        conditions.append("ministerio_agencia_id = @ministerio_agencia_id")
        params["ministerio_agencia_id"] = ministerio_agencia_id

    if categoria:
        conditions.append("categoria_general_nombre = @categoria")
        params["categoria"] = categoria

    if estado:
        conditions.append("estado_nombre = @estado")
        params["estado"] = estado

    if canal_origen:
        conditions.append(
            "(LOWER(canal_origen_nombre) LIKE @canal OR LOWER(canal_origen_raw) LIKE @canal)"
        )
        params["canal"] = f"%{canal_origen.lower()}%"

    if fecha_desde:
        conditions.append("fecha_ingreso >= @fecha_desde")
        params["fecha_desde"] = fecha_desde

    if fecha_hasta:
        conditions.append("fecha_ingreso <= @fecha_hasta")
        params["fecha_hasta"] = fecha_hasta

    # Text search: BM25-style LIKE on detalle + observaciones
    if search_terms:
        text_clauses = []
        for i, term in enumerate(search_terms[:5]):  # max 5 terms
            param_key = f"term_{i}"
            pattern = f"%{term.lower()}%"
            text_clauses.append(
                f"(LOWER(detalle) LIKE @{param_key} OR LOWER(observaciones) LIKE @{param_key} "
                f"OR LOWER(tipo_gestion_nombre) LIKE @{param_key})"
            )
            params[param_key] = pattern
        if text_clauses:
            conditions.append("(" + " OR ".join(text_clauses) + ")")

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    # COUNT query to know total matching rows (without LIMIT)
    count_params = {k: v for k, v in params.items() if k != "limit"}
    count_sql = f"SELECT COUNT(*) AS total FROM `{dataset}.vw_agent_gestiones` {where_clause}"
    count_result = await run_raw_query(count_sql, count_params, tool_name="buscar_gestiones_count")
    total_count = count_result.rows[0]["total"] if count_result.rows else 0

    sql = f"""
        SELECT
            id_gestion,
            fecha_ingreso,
            estado_nombre,
            urgencia_nombre,
            ministerio_agencia_nombre,
            categoria_general_nombre,
            tipo_gestion_nombre,
            detalle,
            departamento,
            localidad,
            canal_origen_nombre,
            dias_abierta,
            dias_resolucion
        FROM `{dataset}.vw_agent_gestiones`
        {where_clause}
        ORDER BY fecha_ingreso ASC
        LIMIT @limit
    """

    result = await run_raw_query(sql, params, tool_name="buscar_gestiones")
    result.total_count = total_count
    result.has_more = total_count > result.row_count
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2: consultar_estadisticas
# ─────────────────────────────────────────────────────────────────────────────

# Schema description injected into the LLM prompt for SQL generation
_BQ_SCHEMA_DESCRIPTION = """\
Tabla BigQuery disponible: `{dataset}.vw_agent_gestiones`

Columnas disponibles:
  id_gestion STRING, nro_expediente STRING
  fecha_ingreso DATE, fecha_finalizacion DATE, anio_ingreso INT, mes_ingreso INT
  estado_nombre STRING  -- valores: 'ARCHIVADO','DERIVADO A SUAC','FINALIZADA','INGRESADO','LISTA PARA INNAUGURAR','NO REMITE SUAC'
  urgencia_nombre STRING  -- valores: 'Alta', 'Media', 'Baja'
  ministerio_agencia_id STRING, ministerio_agencia_nombre STRING
  categoria_general_nombre STRING  -- valores: 'Agua y saneamiento','Infraestructura vial','Obras públicas','Educación','Salud','Desarrollo social','Gestión municipal / institucional','Obra de gas','Obra eléctrica / energía','Cultura / eventos','Deportes','Ayuda a instituciones','Cooperativas y mutuales','Otros / No especificado'
  tipo_gestion_nombre STRING, canal_origen_nombre STRING, canal_origen_raw STRING
  detalle STRING, observaciones STRING
  departamento STRING, localidad STRING
  lat NUMERIC, lon NUMERIC
  costo_estimado NUMERIC, costo_moneda STRING
  es_abierta BOOLEAN, es_finalizada BOOLEAN, es_urgente BOOLEAN
  dias_abierta INT, dias_resolucion INT
  color_semaforo STRING  -- color político de la localidad (ej: 'Azul', 'Amarillo', 'Rojo')
  intendente_jefe_comunal STRING  -- nombre del intendente o jefe comunal
  partido_politico STRING  -- partido político que gobierna la localidad
  electores INT  -- cantidad de electores habilitados en la localidad

Funciones BigQuery útiles: COUNT(*), COUNTIF(condicion), AVG(), ROUND(), SAFE_DIVIDE(), COUNT(DISTINCT col)

IMPORTANTE:
- Solo generá una query SELECT.
- Usá siempre backticks para el nombre de tabla: `{dataset}.vw_agent_gestiones`
- Usá @param para parámetros (ejemplo: WHERE departamento = @departamento)
- SIEMPRE incluí LIMIT (máximo 100 para listados, sin LIMIT para agregaciones)
- No inventes columnas que no están en el esquema
"""


async def consultar_estadisticas(
    pregunta: str,
    departamento: str | None = None,
    localidad: str | None = None,
    ministerio_agencia_id: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    categoria: str | None = None,
    estado: str | None = None,
) -> ToolResult:
    """
    Answers numeric/statistical questions by having the LLM generate SQL,
    then validating and executing it against BigQuery.
    """
    from app.agent.llm_provider import get_provider
    from app.agent.sql_validator import extract_sql, validate
    from app.config import settings

    dataset = settings.bq_dataset

    # Build context block with resolved entities
    entity_lines = []
    params: dict = {}
    if departamento:
        entity_lines.append(f"- departamento = '{departamento}' (usar @departamento en SQL)")
        params["departamento"] = departamento
    if localidad:
        entity_lines.append(f"- localidad = '{localidad}' (usar @localidad en SQL)")
        params["localidad"] = localidad
    if ministerio_agencia_id:
        entity_lines.append(f"- ministerio_agencia_id = '{ministerio_agencia_id}' (usar @ministerio_agencia_id en SQL)")
        params["ministerio_agencia_id"] = ministerio_agencia_id
    if fecha_desde:
        entity_lines.append(f"- fecha_desde = '{fecha_desde}' (usar @fecha_desde en SQL)")
        params["fecha_desde"] = fecha_desde
    if fecha_hasta:
        entity_lines.append(f"- fecha_hasta = '{fecha_hasta}' (usar @fecha_hasta en SQL)")
        params["fecha_hasta"] = fecha_hasta
    if categoria:
        entity_lines.append(f"- categoria = '{categoria}' (usar @categoria en SQL)")
        params["categoria"] = categoria
    if estado:
        entity_lines.append(f"- estado = '{estado}' (usar @estado en SQL)")
        params["estado"] = estado

    entities_block = (
        "Entidades ya resueltas (OBLIGATORIO usar estos parámetros exactos en WHERE):\n" +
        "\n".join(entity_lines)
    ) if entity_lines else "Sin entidades de filtro."

    schema_text = _BQ_SCHEMA_DESCRIPTION.format(dataset=dataset)

    system_prompt = f"""\
Sos un experto en BigQuery SQL para sistemas de gestión territorial.
Tu tarea es generar UNA sola query SQL SELECT válida para responder la pregunta del usuario.

{schema_text}

{entities_block}

Reglas estrictas:
1. Generá SOLO la query SQL, sin explicaciones adicionales.
2. Usá EXACTAMENTE los @parametros indicados en las entidades resueltas (no hardcodees los valores).
3. Si la pregunta pide ranking, usá ORDER BY + LIMIT.
4. Si la pregunta pide total/conteo, usá COUNT(*) o COUNTIF().
5. Si la pregunta pide promedio, usá AVG() o ROUND(AVG(), 1).
6. Encapsulá el SQL en triple backtick: ```sql ... ```
"""
    user_prompt = f"Pregunta: {pregunta}"

    try:
        provider = get_provider()
        response = await provider.chat_with_tools(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[],  # No tools — pure text response
        )
        raw_sql = extract_sql(response.content or "")
    except Exception as exc:
        logger.error("SQL generation failed", error=str(exc))
        return ToolResult(
            tool_name="consultar_estadisticas",
            rows=[],
            row_count=0,
            error=f"Error generando SQL: {exc}",
        )

    # Replace {dataset} placeholder if LLM included it literally
    raw_sql = raw_sql.replace("{dataset}", dataset)

    logger.info("Generated SQL", sql=raw_sql[:200])

    is_valid, msg = validate(raw_sql)
    if not is_valid:
        logger.error("SQL validation failed", reason=msg, sql=raw_sql[:200])
        return ToolResult(
            tool_name="consultar_estadisticas",
            rows=[],
            row_count=0,
            error=f"SQL inválido generado: {msg}",
        )

    return await run_raw_query(raw_sql, params, tool_name="consultar_estadisticas")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3: buscar_por_proximidad
# ─────────────────────────────────────────────────────────────────────────────

async def buscar_por_proximidad(
    lat_ref: float,
    lon_ref: float,
    radio_km: float = 20.0,
    departamento: str | None = None,
    categoria: str | None = None,
    estado: str | None = None,
    ministerio_agencia_id: str | None = None,
    limit: int = DEFAULT_LISTING_LIMIT,
) -> ToolResult:
    """
    Returns gestiones within radio_km of the reference point (lat_ref, lon_ref).
    Uses Haversine formula in BigQuery SQL.
    """
    from app.config import settings

    capped_limit = min(max(1, limit), MAX_LISTING_LIMIT)
    capped_radio = max(1.0, min(radio_km, 500.0))
    dataset = settings.bq_dataset

    extra_conditions = []
    params: dict = {
        "lat_ref": lat_ref,
        "lon_ref": lon_ref,
        "radio_km": capped_radio,
        "limit": capped_limit,
    }

    if departamento:
        extra_conditions.append("AND departamento = @departamento")
        params["departamento"] = departamento
    if categoria:
        extra_conditions.append("AND categoria_general_nombre = @categoria")
        params["categoria"] = categoria
    if estado:
        extra_conditions.append("AND estado_nombre = @estado")
        params["estado"] = estado
    if ministerio_agencia_id:
        extra_conditions.append("AND ministerio_agencia_id = @ministerio_agencia_id")
        params["ministerio_agencia_id"] = ministerio_agencia_id

    extra_sql = " ".join(extra_conditions)

    sql = f"""
        SELECT
            id_gestion,
            fecha_ingreso,
            estado_nombre,
            urgencia_nombre,
            ministerio_agencia_nombre,
            categoria_general_nombre,
            tipo_gestion_nombre,
            detalle,
            departamento,
            localidad,
            lat,
            lon,
            ROUND(
                6371 * ACOS(
                    LEAST(1.0,
                        COS(ACOS(-1) / 180 * @lat_ref) * COS(ACOS(-1) / 180 * lat)
                        * COS(ACOS(-1) / 180 * lon - ACOS(-1) / 180 * @lon_ref)
                        + SIN(ACOS(-1) / 180 * @lat_ref) * SIN(ACOS(-1) / 180 * lat)
                    )
                ), 2
            ) AS distancia_km
        FROM `{dataset}.vw_agent_gestiones`
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          {extra_sql}
          AND (
            6371 * ACOS(
                LEAST(1.0,
                    COS(ACOS(-1) / 180 * @lat_ref) * COS(ACOS(-1) / 180 * lat)
                    * COS(ACOS(-1) / 180 * lon - ACOS(-1) / 180 * @lon_ref)
                    + SIN(ACOS(-1) / 180 * @lat_ref) * SIN(ACOS(-1) / 180 * lat)
                )
            )
          ) <= @radio_km
        ORDER BY distancia_km ASC
        LIMIT @limit
    """

    # COUNT query to know total within radius (without LIMIT)
    count_params = {k: v for k, v in params.items() if k != "limit"}
    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM `{dataset}.vw_agent_gestiones`
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          {extra_sql}
          AND (
            6371 * ACOS(
                LEAST(1.0,
                    COS(ACOS(-1) / 180 * @lat_ref) * COS(ACOS(-1) / 180 * lat)
                    * COS(ACOS(-1) / 180 * lon - ACOS(-1) / 180 * @lon_ref)
                    + SIN(ACOS(-1) / 180 * @lat_ref) * SIN(ACOS(-1) / 180 * lat)
                )
            )
          ) <= @radio_km
    """
    count_result = await run_raw_query(count_sql, count_params, tool_name="buscar_por_proximidad_count")
    total_count = count_result.rows[0]["total"] if count_result.rows else 0

    result = await run_raw_query(sql, params, tool_name="buscar_por_proximidad")
    result.total_count = total_count
    result.has_more = total_count > result.row_count
    return result
