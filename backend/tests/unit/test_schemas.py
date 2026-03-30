import pytest
from pydantic import ValidationError

from app.schemas.agent import AgentRequest, Intent, ParsedQuery


def test_parsed_query_valid():
    pq = ParsedQuery(intent=Intent.TERRITORIAL_LISTING, confidence=0.9)
    assert pq.intent == Intent.TERRITORIAL_LISTING
    assert pq.confidence == 0.9
    assert pq.departamento is None
    assert pq.needs_clarification is False


def test_parsed_query_confidence_bounds():
    with pytest.raises(ValidationError):
        ParsedQuery(intent=Intent.TERRITORIAL_LISTING, confidence=1.5)
    with pytest.raises(ValidationError):
        ParsedQuery(intent=Intent.TERRITORIAL_LISTING, confidence=-0.1)


def test_agent_request_message_length():
    with pytest.raises(ValidationError):
        AgentRequest(message="")
    with pytest.raises(ValidationError):
        AgentRequest(message="x" * 2001)


def test_agent_request_defaults():
    req = AgentRequest(message="test")
    assert req.channel == "api"
    assert req.conversation_id is None
    assert req.user_id is None


def test_intent_enum_values():
    assert Intent.TERRITORIAL_LISTING.value == "territorial_listing"
    assert Intent.UNKNOWN.value == "unknown"
    assert len(Intent) == 10
