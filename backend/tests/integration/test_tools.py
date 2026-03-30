"""
Integration tests for analytics tools.
These tests hit the REAL BigQuery instance.
Requires valid GOOGLE_APPLICATION_CREDENTIALS and GCP_PROJECT_ID in environment.

Run with:
    pytest tests/integration/ -v

Skip if BQ not configured (CI without credentials).
"""

import os

import pytest

from app.tools.analytics_tools import (
    get_general_summary,
    get_gestiones_listing,
    get_open_and_delay_metrics,
    get_ranking_departments,
    get_ranking_localities,
    get_ranking_ministries,
    get_territory_metrics,
)

BQ_CONFIGURED = bool(os.getenv("GCP_PROJECT_ID") and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))

skip_if_no_bq = pytest.mark.skipif(
    not BQ_CONFIGURED,
    reason="BigQuery credentials not configured",
)


@skip_if_no_bq
@pytest.mark.asyncio
async def test_general_summary_returns_data():
    result = await get_general_summary()
    assert result.error is None
    assert result.row_count == 1
    row = result.rows[0]
    assert "total_gestiones" in row
    assert row["total_gestiones"] >= 0


@skip_if_no_bq
@pytest.mark.asyncio
async def test_ranking_departments_schema():
    result = await get_ranking_departments(limit=5)
    assert result.error is None
    assert result.row_count > 0
    row = result.rows[0]
    assert "departamento" in row
    assert "total_gestiones" in row
    assert "urgentes" in row


@skip_if_no_bq
@pytest.mark.asyncio
async def test_ranking_localities_schema():
    result = await get_ranking_localities(limit=5)
    assert result.error is None
    row = result.rows[0]
    assert "localidad" in row
    assert "departamento" in row
    assert "total_gestiones" in row


@skip_if_no_bq
@pytest.mark.asyncio
async def test_metrics_by_department():
    # Use the first available department from the ranking
    ranking = await get_ranking_departments(limit=1)
    if not ranking.rows:
        pytest.skip("No departments in BigQuery")
    department = ranking.rows[0]["departamento"]

    result = await get_territory_metrics(departamento=department)
    assert result.error is None
    assert result.row_count == 1
    row = result.rows[0]
    assert "total_gestiones" in row
    assert "abiertas" in row
    assert "urgentes" in row
    assert "porcentaje_urgentes" in row


@skip_if_no_bq
@pytest.mark.asyncio
async def test_open_and_delay_metrics():
    ranking = await get_ranking_departments(limit=1)
    if not ranking.rows:
        pytest.skip("No departments in BigQuery")
    department = ranking.rows[0]["departamento"]

    result = await get_open_and_delay_metrics(departamento=department)
    assert result.error is None
    assert result.row_count == 1
    row = result.rows[0]
    assert "abiertas" in row
    assert "antiguedad_promedio_abiertas_dias" in row
    assert "demora_promedio_resolucion_dias" in row


@skip_if_no_bq
@pytest.mark.asyncio
async def test_gestiones_listing_has_required_fields():
    ranking = await get_ranking_departments(limit=1)
    if not ranking.rows:
        pytest.skip("No departments in BigQuery")
    department = ranking.rows[0]["departamento"]

    result = await get_gestiones_listing(departamento=department, limit=5)
    assert result.error is None
    if result.rows:
        row = result.rows[0]
        assert "id_gestion" in row
        assert "fecha_ingreso" in row
        assert "estado_nombre" in row
        assert "ministerio_agencia_nombre" in row


@skip_if_no_bq
@pytest.mark.asyncio
async def test_ranking_ministries_global():
    result = await get_ranking_ministries(limit=5)
    assert result.error is None
    assert result.row_count > 0
    row = result.rows[0]
    assert "ministerio_agencia_nombre" in row
    assert "total_gestiones" in row
