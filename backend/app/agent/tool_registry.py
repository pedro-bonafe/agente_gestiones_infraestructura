"""
Tool registry: maps tool names to callables and defines OpenAI function descriptors.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from app.tools.analytics_tools import (
    get_general_summary,
    get_gestiones_listing,
    get_ministry_listing,
    get_ministry_metrics,
    get_ministry_rankings,
    get_open_and_delay_metrics,
    get_ranking_departments,
    get_ranking_localities,
    get_ranking_ministries,
    get_ranking_urgent_localities,
    get_territory_metrics,
    get_urgent_share_by_ministry,
)

ToolCallable = Callable[..., Awaitable[Any]]

TOOL_REGISTRY: dict[str, ToolCallable] = {
    "get_territory_metrics": get_territory_metrics,
    "get_open_and_delay_metrics": get_open_and_delay_metrics,
    "get_ministry_rankings": get_ministry_rankings,
    "get_gestiones_listing": get_gestiones_listing,
    "get_ministry_metrics": get_ministry_metrics,
    "get_ministry_listing": get_ministry_listing,
    "get_urgent_share_by_ministry": get_urgent_share_by_ministry,
    "get_ranking_localities": get_ranking_localities,
    "get_ranking_departments": get_ranking_departments,
    "get_ranking_ministries": get_ranking_ministries,
    "get_ranking_urgent_localities": get_ranking_urgent_localities,
    "get_general_summary": get_general_summary,
}

TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_territory_metrics",
            "description": (
                "Obtiene métricas de gestiones (total, abiertas, finalizadas, urgentes y % urgentes) "
                "para un departamento o localidad específica. Usá esta tool siempre que necesites "
                "las cifras generales de un territorio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {
                        "type": "string",
                        "description": "Nombre del departamento (normalizado, mayúsculas sin acentos)",
                    },
                    "localidad": {
                        "type": "string",
                        "description": "Nombre de la localidad (opcional). Si se provee, filtra a nivel localidad.",
                    },
                },
                "required": ["departamento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_and_delay_metrics",
            "description": (
                "Retorna cantidad de gestiones abiertas, antigüedad promedio de las abiertas "
                "y demora promedio histórica de resolución (en días) para un territorio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {"type": "string"},
                    "localidad": {"type": "string"},
                },
                "required": ["departamento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ministry_rankings",
            "description": (
                "Retorna el ranking de ministerios dentro de un departamento, ordenado por volumen. "
                "Incluye para cada ministerio: total gestiones, urgentes, y demora promedio de resolución. "
                "Usá esta tool para preguntas sobre qué ministerio tiene más gestiones, más demora o más urgencias."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {"type": "string"},
                },
                "required": ["departamento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_gestiones_listing",
            "description": (
                "Retorna un listado de gestiones para un departamento o localidad, "
                "ordenadas por fecha de ingreso más antigua primero. "
                "Incluye: id, fecha, estado, urgencia, ministerio, categoría, tipo, detalle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {"type": "string"},
                    "localidad": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "description": "Cantidad máxima de registros a retornar (default 20, máximo 100)",
                    },
                },
                "required": ["departamento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ministry_metrics",
            "description": (
                "Retorna el CONTEO REAL (total, abiertas, finalizadas, urgentes) de gestiones de un ministerio. "
                "Usá esta tool cuando el usuario pregunte CUÁNTAS gestiones tiene un ministerio "
                "(ej: '¿cuántas gestiones tiene el ministerio X en Y?'). "
                "Devuelve el total exacto sin límite de filas. "
                "Para ver el detalle/listado de las gestiones usá get_ministry_listing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ministerio_agencia_id": {
                        "type": "string",
                        "description": "ID del ministerio (ej: MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS)",
                    },
                    "departamento": {
                        "type": "string",
                        "description": "Nombre del departamento (opcional).",
                    },
                    "localidad": {
                        "type": "string",
                        "description": "Nombre de la localidad (opcional).",
                    },
                },
                "required": ["ministerio_agencia_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ministry_listing",
            "description": (
                "Retorna el LISTADO DETALLADO (id, fecha, estado, detalle) de gestiones de un ministerio. "
                "Usá cuando el usuario quiera VER o LISTAR las gestiones de un ministerio (ej: '¿cuáles son?', 'mostrame'). "
                "ATENCIÓN: retorna máximo 20 registros por defecto (hasta 100). "
                "Para conocer el TOTAL EXACTO de gestiones de un ministerio usá get_ministry_metrics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ministerio_agencia_id": {
                        "type": "string",
                        "description": "ID del ministerio (ej: MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS, MIN_EDUCACION)",
                    },
                    "departamento": {
                        "type": "string",
                        "description": "Nombre del departamento (opcional). Proveer siempre que esté disponible en el contexto.",
                    },
                    "localidad": {
                        "type": "string",
                        "description": "Nombre de la localidad (opcional). Filtrar a nivel localidad si se indica.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Cantidad máxima de registros (default 20, máximo 100)",
                    },
                },
                "required": ["ministerio_agencia_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_urgent_share_by_ministry",
            "description": (
                "Retorna el PORCENTAJE DE URGENCIAS de un ministerio en un territorio. "
                "Usá SOLO cuando el usuario pregunta específicamente por urgencias de un ministerio "
                "(ej: '¿qué % de gestiones urgentes tiene el ministerio X?'). "
                "NO la uses para listar ni contar gestiones en general — para eso usá get_ministry_listing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ministerio_agencia_id": {"type": "string"},
                    "departamento": {"type": "string"},
                    "localidad": {"type": "string"},
                },
                "required": ["ministerio_agencia_id", "departamento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ranking_localities",
            "description": "Retorna el ranking de localidades ordenadas por cantidad total de gestiones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Top N localidades (default 10)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ranking_departments",
            "description": "Retorna el ranking de departamentos ordenados por cantidad total de gestiones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ranking_ministries",
            "description": (
                "Retorna el ranking de ministerios por cantidad de gestiones. "
                "Puede ser global o filtrado por departamento."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "departamento": {"type": "string", "description": "Opcional: filtra por departamento"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ranking_urgent_localities",
            "description": "Retorna el ranking de localidades por cantidad de gestiones urgentes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_general_summary",
            "description": (
                "Retorna un resumen global de todas las gestiones: total, abiertas, finalizadas, "
                "urgentes, cantidad de departamentos y localidades. "
                "Usá para preguntas generales sin filtro territorial."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
