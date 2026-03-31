"""
Catalog service: loads departments, localities, and ministries.
Primary source: BigQuery. Fallback: catalog_snapshot.json.
Cache TTL: 1 hour.
"""

import json
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import structlog

from app.schemas.agent import ToolResult

logger = structlog.get_logger(__name__)

SNAPSHOT_PATH = Path(__file__).parent.parent.parent / "data" / "catalog_snapshot.json"
CACHE_TTL_SECONDS = 3600

_cache_lock = threading.Lock()
_catalog_cache: dict[str, Any] | None = None
_cache_loaded_at: float = 0.0


def _normalize(text: str) -> str:
    """Strip accents, uppercase, collapse whitespace."""
    nfd = unicodedata.normalize("NFD", text)
    without_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(without_accents.upper().split())


def _build_ministry_aliases(ministry_map: dict[str, str]) -> dict[str, str]:
    """Build normalized alias → ministry_id mapping."""
    aliases: dict[str, str] = {}
    prefixes = [
        "MINISTERIO DE",
        "MINISTERIO",
        "AGENCIA DE",
        "AGENCIA",
        "SECRETARIA DE",
        "SECRETARIA",
    ]
    for ministry_id, ministry_name in ministry_map.items():
        normalized = _normalize(ministry_name)
        aliases[normalized] = ministry_id
        # Strip common prefixes to create short aliases
        for prefix in prefixes:
            if normalized.startswith(prefix + " "):
                short = normalized[len(prefix) + 1:].strip()
                if short and short not in aliases:
                    aliases[short] = ministry_id
    return aliases


def _load_snapshot() -> dict[str, Any]:
    """Load catalog from local JSON snapshot."""
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Catalog loaded from snapshot", path=str(SNAPSHOT_PATH))
        return data
    except Exception as exc:
        logger.error("Failed to load catalog snapshot", error=str(exc))
        return {
            "departments": [],
            "localities": [],
            "geographies": [],
            "ministry_map": {},
            "ministry_aliases": {},
        }


def _load_from_bigquery_sync() -> dict[str, Any] | None:
    """Synchronous BigQuery catalog load. Returns None on any failure."""
    try:
        from app.config import settings
        from app.services.bigquery_service import _get_client

        if not settings.bq_configured:
            return None

        client = _get_client()
        dataset = settings.bq_dataset

        def run(sql: str) -> list[dict]:
            rows = list(client.query(sql).result(timeout=15))
            return [dict(row) for row in rows]

        departments = [
            row["departamento"]
            for row in run(f"SELECT DISTINCT departamento FROM `{dataset}.vw_agent_gestiones` WHERE departamento IS NOT NULL ORDER BY departamento")
        ]
        geo_rows = run(
            f"SELECT DISTINCT localidad, departamento FROM `{dataset}.vw_agent_gestiones` WHERE localidad IS NOT NULL ORDER BY departamento, localidad"
        )
        localities = list({row["localidad"] for row in geo_rows})
        geographies = [{"localidad": r["localidad"], "departamento": r["departamento"]} for r in geo_rows]

        ministry_rows = run(
            f"SELECT DISTINCT ministerio_agencia_id, ministerio_agencia_nombre FROM `{dataset}.vw_agent_gestiones` WHERE ministerio_agencia_id IS NOT NULL ORDER BY ministerio_agencia_nombre"
        )
        ministry_map = {r["ministerio_agencia_id"]: r["ministerio_agencia_nombre"] for r in ministry_rows}

        # Load geo_localidades for proximity search (lat_centro, lon_centro per locality)
        geo_localidades: dict[str, dict] = {}
        try:
            geo_rows_coords = run(
                f"SELECT localidad, departamento, lat_centro, lon_centro FROM `{dataset}.geo_localidades` WHERE activo = TRUE AND lat_centro IS NOT NULL"
            )
            for r in geo_rows_coords:
                key = _normalize(r["localidad"])
                geo_localidades[key] = {
                    "localidad": r["localidad"],
                    "departamento": r["departamento"],
                    "lat": float(r["lat_centro"]),
                    "lon": float(r["lon_centro"]),
                }
        except Exception as exc:
            logger.warning("Failed to load geo_localidades, proximity search unavailable", error=str(exc))

        # Merge with snapshot aliases
        snapshot = _load_snapshot()
        ministry_aliases = {**snapshot.get("ministry_aliases", {}), **_build_ministry_aliases(ministry_map)}

        logger.info(
            "Catalog loaded from BigQuery",
            departments=len(departments),
            localities=len(localities),
            ministries=len(ministry_map),
            geo_localities=len(geo_localidades),
        )
        return {
            "departments": departments,
            "localities": localities,
            "geographies": geographies,
            "ministry_map": ministry_map,
            "ministry_aliases": ministry_aliases,
            "geo_localidades": geo_localidades,
            "source": "bigquery",
        }
    except Exception as exc:
        logger.warning("BigQuery catalog load failed, will use snapshot", error=str(exc))
        return None


def get_catalog() -> dict[str, Any]:
    """Return catalog with TTL cache. Thread-safe."""
    global _catalog_cache, _cache_loaded_at

    with _cache_lock:
        now = time.monotonic()
        if _catalog_cache is not None and (now - _cache_loaded_at) < CACHE_TTL_SECONDS:
            return _catalog_cache

        bq_data = _load_from_bigquery_sync()
        if bq_data:
            _catalog_cache = bq_data
        else:
            snapshot = _load_snapshot()
            snapshot.setdefault("ministry_aliases", {})
            snapshot["ministry_aliases"] = {
                **snapshot.get("ministry_aliases", {}),
                **_build_ministry_aliases(snapshot.get("ministry_map", {})),
            }
            snapshot["source"] = "snapshot"
            _catalog_cache = snapshot

        _cache_loaded_at = now
        return _catalog_cache


def invalidate_cache() -> None:
    """Force cache refresh on next call."""
    global _cache_loaded_at
    with _cache_lock:
        _cache_loaded_at = 0.0


def save_catalog_snapshot(catalog: dict[str, Any]) -> None:
    """Persist catalog to snapshot file for future fallback."""
    try:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)
        logger.info("Catalog snapshot saved", path=str(SNAPSHOT_PATH))
    except Exception as exc:
        logger.error("Failed to save catalog snapshot", error=str(exc))
