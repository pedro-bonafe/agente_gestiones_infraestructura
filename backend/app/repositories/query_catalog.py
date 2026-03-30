"""
Catálogo de queries parametrizadas para BigQuery.
Todas las queries usan {dataset} como placeholder para el dataset configurable
y @param para parámetros de BigQuery.
"""

QUERY_CATALOG: dict[str, str] = {
    "metrics_by_department": """
        SELECT
            departamento,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_abierta) AS abiertas,
            COUNTIF(es_finalizada) AS finalizadas,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento
        GROUP BY departamento
    """,
    "metrics_by_locality": """
        SELECT
            departamento,
            localidad,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_abierta) AS abiertas,
            COUNTIF(es_finalizada) AS finalizadas,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento AND localidad = @localidad
        GROUP BY departamento, localidad
    """,
    "open_and_delay_by_department": """
        SELECT
            departamento,
            COUNTIF(es_abierta) AS abiertas,
            ROUND(
                AVG(CASE WHEN es_abierta THEN dias_abierta END), 1
            ) AS antiguedad_promedio_abiertas_dias,
            ROUND(
                AVG(CASE WHEN es_finalizada AND dias_resolucion IS NOT NULL THEN dias_resolucion END), 1
            ) AS demora_promedio_resolucion_dias
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento
        GROUP BY departamento
    """,
    "open_and_delay_by_locality": """
        SELECT
            departamento,
            localidad,
            COUNTIF(es_abierta) AS abiertas,
            ROUND(
                AVG(CASE WHEN es_abierta THEN dias_abierta END), 1
            ) AS antiguedad_promedio_abiertas_dias,
            ROUND(
                AVG(CASE WHEN es_finalizada AND dias_resolucion IS NOT NULL THEN dias_resolucion END), 1
            ) AS demora_promedio_resolucion_dias
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento AND localidad = @localidad
        GROUP BY departamento, localidad
    """,
    "ministry_rankings_by_department": """
        SELECT
            ministerio_agencia_id,
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(
                AVG(CASE WHEN es_finalizada AND dias_resolucion IS NOT NULL THEN dias_resolucion END), 1
            ) AS demora_promedio_resolucion_dias
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento
        GROUP BY ministerio_agencia_id, ministerio_agencia_nombre
        ORDER BY total_gestiones DESC
        LIMIT 10
    """,
    "listing_by_department": """
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
            localidad
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento
        ORDER BY fecha_ingreso ASC
        LIMIT @limit
    """,
    "listing_by_locality": """
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
            localidad
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento AND localidad = @localidad
        ORDER BY fecha_ingreso ASC
        LIMIT @limit
    """,
    "listing_by_ministry_global": """
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
            localidad
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
        ORDER BY fecha_ingreso ASC
        LIMIT @limit
    """,
    "listing_by_ministry_department": """
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
            localidad
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
          AND departamento = @departamento
        ORDER BY fecha_ingreso ASC
        LIMIT @limit
    """,
    "listing_by_ministry_locality": """
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
            localidad
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
          AND departamento = @departamento
          AND localidad = @localidad
        ORDER BY fecha_ingreso ASC
        LIMIT @limit
    """,
    "metrics_by_ministry_department": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_abierta) AS abiertas,
            COUNTIF(es_finalizada) AS finalizadas,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
          AND departamento = @departamento
        GROUP BY ministerio_agencia_nombre
    """,
    "metrics_by_ministry_locality": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_abierta) AS abiertas,
            COUNTIF(es_finalizada) AS finalizadas,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
          AND departamento = @departamento
          AND localidad = @localidad
        GROUP BY ministerio_agencia_nombre
    """,
    "metrics_by_ministry_global": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_abierta) AS abiertas,
            COUNTIF(es_finalizada) AS finalizadas,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
        GROUP BY ministerio_agencia_nombre
    """,
    "urgent_share_by_ministry_department": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
          AND departamento = @departamento
        GROUP BY ministerio_agencia_nombre
    """,
    "urgent_share_by_ministry_locality": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id = @ministerio_agencia_id
          AND departamento = @departamento
          AND localidad = @localidad
        GROUP BY ministerio_agencia_nombre
    """,
    "ranking_localities": """
        SELECT
            localidad,
            departamento,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes
        FROM `{dataset}.vw_agent_gestiones`
        GROUP BY localidad, departamento
        ORDER BY total_gestiones DESC
        LIMIT @limit
    """,
    "ranking_departments": """
        SELECT
            departamento,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes,
            COUNTIF(es_abierta) AS abiertas
        FROM `{dataset}.vw_agent_gestiones`
        GROUP BY departamento
        ORDER BY total_gestiones DESC
        LIMIT @limit
    """,
    "ranking_ministries_global": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes,
            COUNTIF(es_abierta) AS abiertas
        FROM `{dataset}.vw_agent_gestiones`
        GROUP BY ministerio_agencia_nombre
        ORDER BY total_gestiones DESC
        LIMIT @limit
    """,
    "ranking_ministries_by_department": """
        SELECT
            ministerio_agencia_nombre,
            COUNT(*) AS total_gestiones,
            COUNTIF(es_urgente) AS urgentes,
            COUNTIF(es_abierta) AS abiertas
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento = @departamento
        GROUP BY ministerio_agencia_nombre
        ORDER BY total_gestiones DESC
        LIMIT @limit
    """,
    "ranking_urgent_localities": """
        SELECT
            localidad,
            departamento,
            COUNTIF(es_urgente) AS urgentes,
            COUNT(*) AS total_gestiones,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes
        FROM `{dataset}.vw_agent_gestiones`
        GROUP BY localidad, departamento
        ORDER BY urgentes DESC
        LIMIT @limit
    """,
    "general_summary": """
        SELECT
            COUNT(*) AS total_gestiones,
            COUNTIF(es_abierta) AS abiertas,
            COUNTIF(es_finalizada) AS finalizadas,
            COUNTIF(es_urgente) AS urgentes,
            ROUND(SAFE_DIVIDE(COUNTIF(es_urgente), COUNT(*)) * 100, 1) AS porcentaje_urgentes,
            COUNT(DISTINCT departamento) AS departamentos,
            COUNT(DISTINCT localidad) AS localidades
        FROM `{dataset}.vw_agent_gestiones`
    """,
    # Catalog queries
    "list_departments": """
        SELECT DISTINCT departamento
        FROM `{dataset}.vw_agent_gestiones`
        WHERE departamento IS NOT NULL
        ORDER BY departamento
    """,
    "list_localities": """
        SELECT DISTINCT localidad, departamento
        FROM `{dataset}.vw_agent_gestiones`
        WHERE localidad IS NOT NULL
        ORDER BY departamento, localidad
    """,
    "list_ministries": """
        SELECT DISTINCT ministerio_agencia_id, ministerio_agencia_nombre
        FROM `{dataset}.vw_agent_gestiones`
        WHERE ministerio_agencia_id IS NOT NULL
        ORDER BY ministerio_agencia_nombre
    """,
}
