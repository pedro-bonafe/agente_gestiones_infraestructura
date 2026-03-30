import pytest

from app.agent.catalog_resolver import resolve_entities
from app.schemas.agent import Intent, ParsedQuery

MOCK_CATALOG = {
    "departments": [
        "CALAMUCHITA",
        "COLON",
        "CRUZ DEL EJE",
        "MARCOS JUAREZ",
        "RIO SEGUNDO",
    ],
    "localities": [
        "CALMAYO",
        "EMBALSE",
        "GENERAL PAZ",
        "SAIRA",
        "LAS ISLETILLAS",
    ],
    "geographies": [
        {"localidad": "CALMAYO", "departamento": "CALAMUCHITA"},
        {"localidad": "EMBALSE", "departamento": "CALAMUCHITA"},
        {"localidad": "GENERAL PAZ", "departamento": "COLON"},
        {"localidad": "SAIRA", "departamento": "CRUZ DEL EJE"},
        {"localidad": "LAS ISLETILLAS", "departamento": "RIO SEGUNDO"},
    ],
    "ministry_map": {
        "MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS": "Ministerio de Infraestructura y Servicios Públicos",
        "MIN_AMBIENTE": "Ministerio de Ambiente",
        "MIN_EDUCACION": "Ministerio de Educación",
    },
    "ministry_aliases": {
        "MINISTERIO DE INFRAESTRUCTURA": "MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS",
        "OBRAS PUBLICAS": "MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS",
        "INFRAESTRUCTURA": "MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS",
        "MINISTERIO DE INFRAESTRUCTURA Y SERVICIOS PUBLICOS": "MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS",
        "AMBIENTE": "MIN_AMBIENTE",
        "MINISTERIO DE AMBIENTE": "MIN_AMBIENTE",
        "EDUCACION": "MIN_EDUCACION",
    },
}


def _make_query(intent: Intent = Intent.TERRITORIAL_LISTING, **kwargs) -> ParsedQuery:
    return ParsedQuery(intent=intent, confidence=0.9, **kwargs)


def test_resolves_department_exact():
    q = _make_query(departamento="CALAMUCHITA")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.departamento == "CALAMUCHITA"


def test_resolves_department_lowercase_and_accented():
    q = _make_query(departamento="calamuchita")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.departamento == "CALAMUCHITA"


def test_resolves_department_partial_match():
    q = _make_query(departamento="Cruz del Eje")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.departamento == "CRUZ DEL EJE"


def test_infers_department_from_locality():
    q = _make_query(localidad="CALMAYO")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.localidad == "CALMAYO"
    assert resolved.departamento == "CALAMUCHITA"


def test_clarification_on_missing_territory():
    q = _make_query(intent=Intent.TERRITORIAL_LISTING)
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.needs_clarification is True
    assert resolved.clarification_question is not None


def test_resolves_ministry_alias():
    q = _make_query(ministerio_nombre="Obras Públicas")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.ministerio_agencia_id == "MIN_INFRAESTRUCTURA_SERVICIOS_PUBLICOS"


def test_resolves_ministry_short_alias():
    q = _make_query(ministerio_nombre="Ambiente")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.ministerio_agencia_id == "MIN_AMBIENTE"


def test_ranking_intent_no_territory_no_clarification():
    q = _make_query(intent=Intent.RANKING_LOCALIDADES)
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.needs_clarification is False


def test_ministry_listing_without_ministry_triggers_clarification():
    q = _make_query(intent=Intent.MINISTRY_TERRITORIAL_LISTING, departamento="COLON")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.needs_clarification is True


def test_unknown_department_returns_none():
    q = _make_query(departamento="ATLANTIDA")
    resolved = resolve_entities(q, MOCK_CATALOG)
    assert resolved.departamento is None
