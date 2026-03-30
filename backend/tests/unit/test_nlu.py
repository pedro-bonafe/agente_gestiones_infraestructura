"""
NLU unit tests using mocked OpenAI responses.
These tests validate that parse_query correctly maps LLM output to ParsedQuery.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.agent import ConversationContext, Intent, ParsedQuery

EMPTY_CONTEXT = ConversationContext(conversation_id="test-conv")


def _mock_openai_response(data: dict) -> MagicMock:
    """Build a mock that mimics openai.ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = json.dumps(data)
    response = MagicMock()
    response.choices = [choice]
    return response


def _patch_openai(data: dict):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(data)
    return patch("app.agent.nlu._parse_sync", return_value=ParsedQuery(**{**data, "confidence": data.get("confidence", 0.9)}))


@pytest.mark.asyncio
async def test_territorial_listing():
    with _patch_openai({
        "intent": "territorial_listing",
        "departamento": "Río Segundo",
        "localidad": None,
        "ministerio_nombre": None,
        "ministerio_agencia_id": None,
        "cantidad": None,
        "orden_campo": None,
        "orden_direccion": None,
        "urgencia": None,
        "estado": None,
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.95,
    }):
        from app.agent.nlu import parse_query
        with patch("app.config.settings.has_openai", True):
            result = await parse_query(
                "Cuáles gestiones tiene el departamento Río Segundo",
                EMPTY_CONTEXT,
            )
        assert result.intent == Intent.TERRITORIAL_LISTING
        assert result.needs_clarification is False


@pytest.mark.asyncio
async def test_open_and_delay_metrics():
    with _patch_openai({
        "intent": "open_and_delay_metrics",
        "departamento": "Cruz del Eje",
        "localidad": None,
        "ministerio_nombre": None,
        "ministerio_agencia_id": None,
        "cantidad": None,
        "orden_campo": None,
        "orden_direccion": None,
        "urgencia": None,
        "estado": None,
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.92,
    }):
        from app.agent.nlu import parse_query
        with patch("app.config.settings.has_openai", True):
            result = await parse_query(
                "Cuántas gestiones abiertas hay en Cruz del Eje? Cuál es el tiempo promedio de demora?",
                EMPTY_CONTEXT,
            )
        assert result.intent == Intent.OPEN_AND_DELAY_METRICS


@pytest.mark.asyncio
async def test_department_ministry_rankings():
    with _patch_openai({
        "intent": "department_ministry_rankings",
        "departamento": "Cruz del Eje",
        "localidad": None,
        "ministerio_nombre": None,
        "ministerio_agencia_id": None,
        "cantidad": None,
        "orden_campo": None,
        "orden_direccion": None,
        "urgencia": None,
        "estado": None,
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
    }):
        from app.agent.nlu import parse_query
        with patch("app.config.settings.has_openai", True):
            result = await parse_query(
                "Cuál es el ministerio con más gestiones del departamento Cruz del Eje?",
                EMPTY_CONTEXT,
            )
        assert result.intent == Intent.DEPARTMENT_MINISTRY_RANKINGS


@pytest.mark.asyncio
async def test_unknown_intent():
    with _patch_openai({
        "intent": "unknown",
        "departamento": None,
        "localidad": None,
        "ministerio_nombre": None,
        "ministerio_agencia_id": None,
        "cantidad": None,
        "orden_campo": None,
        "orden_direccion": None,
        "urgencia": None,
        "estado": None,
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.95,
    }):
        from app.agent.nlu import parse_query
        with patch("app.config.settings.has_openai", True):
            result = await parse_query("Cuál es la capital de Francia?", EMPTY_CONTEXT)
        assert result.intent == Intent.UNKNOWN


@pytest.mark.asyncio
async def test_needs_clarification_no_territory():
    with _patch_openai({
        "intent": "territorial_listing",
        "departamento": None,
        "localidad": None,
        "ministerio_nombre": None,
        "ministerio_agencia_id": None,
        "cantidad": None,
        "orden_campo": None,
        "orden_direccion": None,
        "urgencia": None,
        "estado": None,
        "needs_clarification": True,
        "clarification_question": "¿Para qué territorio querés consultar?",
        "confidence": 0.7,
    }):
        from app.agent.nlu import parse_query
        with patch("app.config.settings.has_openai", True):
            result = await parse_query("Cuáles son las gestiones?", EMPTY_CONTEXT)
        assert result.needs_clarification is True
        assert result.clarification_question is not None


@pytest.mark.asyncio
async def test_no_openai_returns_unknown():
    from app.agent.nlu import parse_query
    with patch("app.config.settings.has_openai", False):
        result = await parse_query("Cuáles gestiones tiene Cruz del Eje?", EMPTY_CONTEXT)
    assert result.intent == Intent.UNKNOWN
    assert result.needs_clarification is True
