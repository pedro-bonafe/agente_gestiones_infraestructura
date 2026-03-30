from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    TERRITORIAL_LISTING = "territorial_listing"
    OPEN_AND_DELAY_METRICS = "open_and_delay_metrics"
    DEPARTMENT_MINISTRY_RANKINGS = "department_ministry_rankings"
    MINISTRY_TERRITORIAL_LISTING = "ministry_territorial_listing"
    RANKING_LOCALIDADES = "ranking_localidades"
    RANKING_DEPARTAMENTOS = "ranking_departamentos"
    RANKING_MINISTERIOS = "ranking_ministerios"
    RANKING_URGENCIAS_LOCALIDAD = "ranking_urgencias_localidad"
    RESUMEN_GENERAL = "resumen_general"
    UNKNOWN = "unknown"


class ParsedQuery(BaseModel):
    intent: Intent
    departamento: str | None = None
    localidad: str | None = None
    ministerio_agencia_id: str | None = None
    ministerio_nombre: str | None = None
    cantidad: int | None = Field(None, ge=1, le=100)
    orden_campo: Literal["fecha"] | None = None
    orden_direccion: Literal["ASC", "DESC"] | None = None
    urgencia: str | None = None
    estado: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class ToolResult(BaseModel):
    tool_name: str
    rows: list[dict]
    row_count: int
    error: str | None = None
    query_ms: float | None = None


class ConversationContext(BaseModel):
    conversation_id: str
    last_intent: Intent | None = None
    last_entities: dict = Field(default_factory=dict)
    last_answer: str | None = None
    turn_count: int = 0


class AgentRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = None
    conversation_id: str | None = None
    channel: Literal["api", "telegram"] = "api"


class AgentResult(BaseModel):
    answer: str
    intent: str
    entities: dict
    tools_used: list[str]
    confidence: float
    needs_clarification: bool = False


class AgentResponse(BaseModel):
    answer: str
    intent: str
    entities: dict
    tools_used: list[str]
    confidence: float
