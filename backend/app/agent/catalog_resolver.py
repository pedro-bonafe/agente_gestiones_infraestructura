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
    Intent.BUSCAR_LISTADO,
    # consultar_numerico does NOT require territory — global summaries/rankings are valid
    Intent.BUSCAR_POR_PROXIMIDAD,
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
    Also resolves lat/lon for proximity search.
    """
    departments: list[str] = catalog.get("departments", [])
    localities: list[str] = catalog.get("localities", [])
    geographies: list[dict] = catalog.get("geographies", [])
    ministry_aliases: dict[str, str] = catalog.get("ministry_aliases", {})
    ministry_map: dict[str, str] = catalog.get("ministry_map", {})
    geo_localidades: dict[str, dict] = catalog.get("geo_localidades", {})

    resolved = parsed.model_copy()

    # --- Resolve department ---
    dep_match, dep_score = _best_match(parsed.departamento, departments)
    resolved.departamento = dep_match

    # --- Resolve locality ---
    loc_match, loc_score = _best_match(parsed.localidad, localities)
    resolved.localidad = loc_match

    # --- Fallback: if localidad didn't match any locality but matches a department, use it as department ---
    if parsed.localidad and not loc_match and not dep_match:
        dep_fallback, dep_fallback_score = _best_match(parsed.localidad, departments)
        if dep_fallback:
            resolved.departamento = dep_fallback
            dep_match = dep_fallback
            dep_score = dep_fallback_score
            logger.info(
                "Locality string resolved as department (fallback)",
                raw=parsed.localidad,
                departamento=dep_fallback,
                score=round(dep_fallback_score, 2),
            )

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

    # --- For proximity search: resolve lat/lon from locality name ---
    if parsed.intent == Intent.BUSCAR_POR_PROXIMIDAD:
        ref_locality = loc_match or parsed.localidad
        if ref_locality:
            norm_key = _normalize(ref_locality)
            geo_entry = geo_localidades.get(norm_key)

            # Fallback: fuzzy search in geo_localidades keys
            if not geo_entry:
                best_geo_key, best_geo_score = _best_match(ref_locality, list(geo_localidades.keys()))
                if best_geo_key and best_geo_score >= MATCH_THRESHOLD:
                    geo_entry = geo_localidades[best_geo_key]

            if geo_entry:
                resolved.geo_lat = geo_entry["lat"]
                resolved.geo_lon = geo_entry["lon"]
                # If locality wasn't found in gestiones, still set it from geo_localidades
                if not resolved.localidad:
                    resolved.localidad = geo_entry["localidad"]
                if not resolved.departamento:
                    resolved.departamento = geo_entry.get("departamento")
                logger.info(
                    "Geo coords resolved for proximity",
                    localidad=ref_locality,
                    lat=geo_entry["lat"],
                    lon=geo_entry["lon"],
                )
            else:
                logger.warning("No geo coords found for locality", localidad=ref_locality)

    # --- Validate: territory required for certain intents ---
    if parsed.intent in INTENTS_REQUIRING_TERRITORY:
        if not resolved.departamento and not resolved.localidad:
            if not resolved.needs_clarification:
                resolved.needs_clarification = True
                resolved.clarification_question = (
                    "¿Para qué territorio querés consultar? Por favor indicá el departamento o localidad."
                )

    # --- Validate: proximity requires a reference locality ---
    if parsed.intent == Intent.BUSCAR_POR_PROXIMIDAD and not resolved.localidad:
        if not resolved.needs_clarification:
            resolved.needs_clarification = True
            resolved.clarification_question = (
                "¿Desde qué localidad querés buscar gestiones cercanas?"
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
