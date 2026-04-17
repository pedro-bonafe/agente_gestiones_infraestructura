from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    BUSCAR_LISTADO = "buscar_listado"
    CONSULTAR_NUMERICO = "consultar_numerico"
    BUSCAR_POR_PROXIMIDAD = "buscar_por_proximidad"
    CONSULTAR_INFO_POLITICA = "consultar_info_politica"
    UNKNOWN = "unknown"


class ParsedQuery(BaseModel):
    intent: Intent
    # Territory entities
    departamento: str | None = None
    localidad: str | None = None
    ministerio_agencia_id: str | None = None
    ministerio_nombre: str | None = None
    # New filter fields
    search_terms: list[str] = Field(default_factory=list)
    categoria: str | None = None
    estado: str | None = None
    canal_origen: str | None = None
    fecha_desde: str | None = None    # raw string, resolved by date_resolver
    fecha_hasta: str | None = None    # raw string, resolved by date_resolver
    radio_km: float | None = None
    cantidad: int | None = Field(None, ge=1, le=100)
    # Resolved geo coords (set by catalog_resolver for proximity search)
    geo_lat: float | None = None
    geo_lon: float | None = None
    # NLU metadata
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class ToolResult(BaseModel):
    tool_name: str
    rows: list[dict]
    row_count: int
    total_count: int | None = None   # total matching rows (ignoring LIMIT)
    has_more: bool = False           # True when total_count > row_count
    error: str | None = None
    query_ms: float | None = None


class Turn(BaseModel):
    message: str
    intent: str
    entities: dict
    answer: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ConversationContext(BaseModel):
    conversation_id: str
    turns: list[Turn] = Field(default_factory=list)
    turn_count: int = 0

    @property
    def last_intent(self) -> str | None:
        return self.turns[-1].intent if self.turns else None

    @property
    def last_entities(self) -> dict:
        return self.turns[-1].entities if self.turns else {}

    @property
    def last_answer(self) -> str | None:
        return self.turns[-1].answer if self.turns else None

    def recent_turns(self, n: int = 3) -> list[Turn]:
        return self.turns[-n:] if self.turns else []


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
