"""
Tool registry V2: 3 tools covering all query types.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from app.tools.analytics_tools import (
    buscar_gestiones,
    buscar_por_proximidad,
    consultar_estadisticas,
)

ToolCallable = Callable[..., Awaitable[Any]]

TOOL_REGISTRY: dict[str, ToolCallable] = {
    "buscar_gestiones": buscar_gestiones,
    "consultar_estadisticas": consultar_estadisticas,
    "buscar_por_proximidad": buscar_por_proximidad,
}

TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "buscar_gestiones",
            "description": (
                "Busca y lista gestiones concretas con sus detalles. "
                "Usá cuando el usuario quiere VER gestiones: '¿cuáles son?', 'mostrame', 'listame'. "
                "Soporta filtros exactos (territorio, ministerio, estado, categoría, canal, fechas) "
                "y búsqueda de texto libre en el contenido de las gestiones (detalle, tipo). "
                "Devuelve hasta 100 registros con id, fecha, estado, urgencia, ministerio, categoría, detalle, localidad."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {
                        "type": "string",
                        "description": "Nombre del departamento (normalizado, con acentos exactos como en BQ).",
                    },
                    "localidad": {
                        "type": "string",
                        "description": "Nombre de la localidad (opcional). Filtra a nivel localidad.",
                    },
                    "ministerio_agencia_id": {
                        "type": "string",
                        "description": "ID del ministerio (ej: MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS).",
                    },
                    "search_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Términos de búsqueda temática en el contenido (ej: ['pavimento', 'asfalto']). Máximo 5.",
                    },
                    "categoria": {
                        "type": "string",
                        "description": "Categoría exacta (ej: 'Infraestructura vial', 'Agua y saneamiento').",
                    },
                    "estado": {
                        "type": "string",
                        "description": "Estado de la gestión (ej: 'INGRESADO', 'NO REMITE SUAC', 'FINALIZADA').",
                    },
                    "canal_origen": {
                        "type": "string",
                        "description": "Canal de origen (ej: 'WHATSAPP', 'MAIL').",
                    },
                    "fecha_desde": {
                        "type": "string",
                        "description": "Fecha de inicio en formato YYYY-MM-DD.",
                    },
                    "fecha_hasta": {
                        "type": "string",
                        "description": "Fecha de fin en formato YYYY-MM-DD.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo registros a devolver (default 20, máximo 100).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_estadisticas",
            "description": (
                "Responde preguntas numéricas, estadísticas y de ranking sobre gestiones. "
                "Usá para: '¿cuántas?', '¿qué porcentaje?', 'ranking de', 'promedio de días', "
                "'tiempo de resolución', 'resumen', 'comparativa entre ministerios'. "
                "Internamente genera SQL SELECT optimizado para la pregunta específica. "
                "Puede combinar múltiples filtros: territorio + ministerio + estado + categoría + fechas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pregunta": {
                        "type": "string",
                        "description": "La pregunta estadística en lenguaje natural (ej: '¿Cuántas gestiones hay por categoría en Santa María?').",
                    },
                    "departamento": {
                        "type": "string",
                        "description": "Nombre del departamento (normalizado).",
                    },
                    "localidad": {
                        "type": "string",
                        "description": "Nombre de la localidad (opcional).",
                    },
                    "ministerio_agencia_id": {
                        "type": "string",
                        "description": "ID del ministerio (opcional).",
                    },
                    "fecha_desde": {
                        "type": "string",
                        "description": "Fecha de inicio YYYY-MM-DD.",
                    },
                    "fecha_hasta": {
                        "type": "string",
                        "description": "Fecha de fin YYYY-MM-DD.",
                    },
                    "categoria": {
                        "type": "string",
                        "description": "Categoría de gestión (opcional).",
                    },
                    "estado": {
                        "type": "string",
                        "description": "Estado de la gestión (opcional).",
                    },
                },
                "required": ["pregunta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_por_proximidad",
            "description": (
                "Busca gestiones geográficamente cercanas a un punto de referencia. "
                "Usá cuando el usuario menciona 'cerca de', 'en un radio de', 'a X km de'. "
                "Devuelve gestiones ordenadas por distancia ascendente, con campo distancia_km."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lat_ref": {
                        "type": "number",
                        "description": "Latitud del punto de referencia (grados decimales).",
                    },
                    "lon_ref": {
                        "type": "number",
                        "description": "Longitud del punto de referencia (grados decimales).",
                    },
                    "radio_km": {
                        "type": "number",
                        "description": "Radio de búsqueda en kilómetros (default 20, máximo 500).",
                    },
                    "departamento": {"type": "string", "description": "Filtro opcional por departamento."},
                    "categoria": {"type": "string", "description": "Filtro opcional por categoría."},
                    "estado": {"type": "string", "description": "Filtro opcional por estado."},
                    "ministerio_agencia_id": {"type": "string", "description": "Filtro opcional por ministerio."},
                    "limit": {"type": "integer", "description": "Máximo de resultados (default 20)."},
                },
                "required": ["lat_ref", "lon_ref"],
            },
        },
    },
]
