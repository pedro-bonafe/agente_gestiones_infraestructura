"""
Resolves raw entity strings from the NLU against the catalog.
Uses fuzzy matching with SequenceMatcher.
"""

import unicodedata
from difflib import SequenceMatcher
from typing import Any

import structlog

from app.schemas.agent import Intent, ParsedQuery

logger = structlog.get_logger(__name__)

MATCH_THRESHOLD = 0.80
INTENTS_REQUIRING_TERRITORY = {
    Intent.TERRITORIAL_LISTING,
    Intent.OPEN_AND_DELAY_METRICS,
    Intent.DEPARTMENT_MINISTRY_RANKINGS,
    Intent.MINISTRY_TERRITORIAL_LISTING,
}


def _normalize(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    without_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(without_accents.upper().split())


def _score(raw: str, candidate: str) -> float:
    r = _normalize(raw)
    c = _normalize(candidate)
    if r == c:
        return 1.0
    if r in c or c in r:
        return 0.95
    return SequenceMatcher(None, r, c).ratio()


def _best_match(raw: str | None, options: list[str]) -> tuple[str | None, float]:
    if not raw:
        return None, 0.0
    best_val, best_score = None, 0.0
    for opt in options:
        s = _score(raw, opt)
        if s > best_score:
            best_score = s
            best_val = opt
    if best_score >= MATCH_THRESHOLD:
        return best_val, best_score
    return None, best_score


def _resolve_ministry(raw: str | None, aliases: dict[str, str]) -> tuple[str | None, str | None]:
    """Returns (ministerio_agencia_id, best_alias_matched)."""
    if not raw:
        return None, None
    norm = _normalize(raw)
    # Exact match first
    if norm in aliases:
        return aliases[norm], norm
    # Fuzzy match against alias keys
    best_key, best_score = _best_match(raw, list(aliases.keys()))
    if best_key and best_score >= MATCH_THRESHOLD:
        return aliases[best_key], best_key
    return None, None


def resolve_entities(parsed: ParsedQuery, catalog: dict[str, Any]) -> ParsedQuery:
    """
    Resolve raw entity strings against the catalog.
    Returns updated ParsedQuery with resolved, normalized values.
    """
    departments: list[str] = catalog.get("departments", [])
    localities: list[str] = catalog.get("localities", [])
    geographies: list[dict] = catalog.get("geographies", [])
    ministry_aliases: dict[str, str] = catalog.get("ministry_aliases", {})
    ministry_map: dict[str, str] = catalog.get("ministry_map", {})

    resolved = parsed.model_copy()

    # --- Resolve department ---
    dep_match, dep_score = _best_match(parsed.departamento, departments)
    resolved.departamento = dep_match

    # --- Resolve locality ---
    loc_match, loc_score = _best_match(parsed.localidad, localities)
    resolved.localidad = loc_match

    # --- Infer department from locality if not provided ---
    if loc_match and not dep_match:
        candidate_deps = list(
            dict.fromkeys(
                g["departamento"] for g in geographies
                if _normalize(g["localidad"]) == _normalize(loc_match)
            )
        )
        if len(candidate_deps) == 1:
            resolved.departamento = candidate_deps[0]
            logger.info(
                "Department inferred from locality",
                localidad=loc_match,
                departamento=candidate_deps[0],
            )
        elif len(candidate_deps) > 1:
            resolved.needs_clarification = True
            resolved.clarification_question = (
                f"La localidad {loc_match} está en más de un departamento "
                f"({', '.join(candidate_deps)}). ¿En cuál departamento querés consultar?"
            )
            logger.info("Ambiguous locality", localidad=loc_match, candidates=candidate_deps)

    # --- Resolve ministry ---
    ministry_id, matched_alias = _resolve_ministry(
        parsed.ministerio_nombre or parsed.ministerio_agencia_id,
        ministry_aliases,
    )
    resolved.ministerio_agencia_id = ministry_id
    if ministry_id:
        resolved.ministerio_nombre = ministry_map.get(ministry_id, parsed.ministerio_nombre)

    # --- Validate: territory required for certain intents ---
    if parsed.intent in INTENTS_REQUIRING_TERRITORY:
        if not resolved.departamento and not resolved.localidad:
            if not resolved.needs_clarification:
                resolved.needs_clarification = True
                resolved.clarification_question = (
                    "¿Para qué territorio querés consultar? Por favor indicá el departamento o localidad."
                )

    # --- Validate: ministry required for ministry_territorial_listing ---
    if parsed.intent == Intent.MINISTRY_TERRITORIAL_LISTING and not resolved.ministerio_agencia_id:
        if not resolved.needs_clarification:
            resolved.needs_clarification = True
            resolved.clarification_question = (
                "¿A qué ministerio o agencia te referís?"
            )

    logger.info(
        "Entities resolved",
        intent=parsed.intent,
        dep_raw=parsed.departamento,
        dep_resolved=resolved.departamento,
        dep_score=round(dep_score, 2),
        loc_raw=parsed.localidad,
        loc_resolved=resolved.localidad,
        ministry_id=resolved.ministerio_agencia_id,
        needs_clarification=resolved.needs_clarification,
    )
    return resolved
