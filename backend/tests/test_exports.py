"""Exportaciones para el contador: CSV (BOM, encabezados, inyección de fórmulas), PDF y endpoints."""

import csv
import io
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.export_api import router
from app.exports import BOM, EXPORTS, Column, format_cell, to_csv
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders
from app.pdf_reports import latin1, money, pnl_pdf, summary_pdf

TODAY = datetime.now(UTC).date()

# App mínima solo con este router: la prueba no depende de cómo main.py lo registre.
app = FastAPI()
app.include_router(router)


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


def parse_csv(raw: bytes) -> list[list[str]]:
    assert raw.startswith(BOM.encode("utf-8"))
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


# ------------------------------------------------------------------------------ CSV (puro)


def test_csv_has_bom_readable_headers_and_formatted_cells():
    columns = [
        Column("name", "Producto"),
        Column("sales", "Ventas", "money"),
        Column("margin", "Margen (%)", "pct"),
        Column("units", "Unidades", "int"),
        Column("day", "Fecha", "date"),
        Column("ok", "¿Activo?", "bool"),
    ]
    rows = [
        {"name": "Árbol, grande", "sales": 1234.5, "margin": 0.1234, "units": 3.0, "day": date(2026, 11, 2), "ok": True},
        {"name": None, "sales": None, "margin": float("nan"), "units": None, "day": None, "ok": False},
    ]
    raw = to_csv(columns, rows)
    assert raw[:3] == b"\xef\xbb\xbf"
    table = parse_csv(raw)
    assert table[0] == ["Producto", "Ventas", "Margen (%)", "Unidades", "Fecha", "¿Activo?"]
    assert table[1] == ["Árbol, grande", "1234.50", "12.34", "3", "2026-11-02", "Sí"]
    assert table[2] == ["", "", "", "", "", "No"]


@pytest.mark.parametrize("text", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "\r=x"])
def test_csv_neutralizes_formula_injection(text):
    cell = parse_csv(to_csv([Column("t", "T")], [{"t": text}]))[1][0]
    assert cell.startswith("'") and cell[1:] == text


def test_csv_does_not_alter_normal_text_or_negative_numbers():
    assert format_cell("Taza -azul", "text") == "Taza -azul"
    assert format_cell(-5.5, "money") == "-5.50"


def test_csv_empty_rows_only_header():
    assert parse_csv(to_csv([Column("a", "A")], [])) == [["A"]]


# ------------------------------------------------------------------------------ PDF


def _pnl_report(name: str = "Árbol “Navidad” – edición 2026…") -> dict:
    zeros = dict.fromkeys(
        ["gross_sales", "promo", "net_sales", "platform_fees", "shipping_cost", "refund_amount", "cogs",
         "gross_profit", "ad_spend", "contribution", "expenses", "net_profit"], 0.0
    )
    row = {**zeros, "gross_sales": 1000.0, "net_sales": 1000.0, "net_profit": 250.0, "margin": 0.25}
    month = {
        "month": "2026-11-01",
        "channels": [{"channel": "walmart", **row}],
        "total": row,
        "unallocated_expenses": 5.0,
        "expenses_by_category": [{"category": name, "amount": 5.0}],
    }
    return {"start": "2026-11-01", "end": "2026-11-01", "months": [month], "summary": row, "note": f"Nota − {name}"}


def test_latin1_sanitizes_typographic_characters():
    assert latin1("“hola” – ‘x’ … − €") == '"hola" - \'x\' ... - EUR'
    assert latin1("日本") == "??"
    assert money(-1234.5) == "-$1,234.50" and money(None) == "-"


def test_pnl_pdf_is_valid_with_accents_and_typographic_characters():
    data = pnl_pdf(_pnl_report(), channel="walmart", generated_at=datetime(2026, 11, 23, tzinfo=UTC))
    assert data.startswith(b"%PDF") and len(data) > 1000


def test_summary_pdf_with_and_without_quality_section():
    total = _pnl_report()["summary"]
    base = {
        "start": "2026-11-01", "end": "2026-11-01", "channel": None, "total": total,
        "by_channel": {"walmart": total}, "returns_days": 30, "note": "Nota “importante” — ñandú",
        "returns": {"cases": 2, "units": 3, "refund": 10.0, "shipping_lost": 4.0, "product_lost": 6.0, "real_cost": 10.0},
        "quality": [],
    }
    assert summary_pdf(base).startswith(b"%PDF")
    with_quality = {**base, "quality": [{"severity": "critical", "title": "Sin costo — ¡ojo!", "detail": "50 SKU…"}]}
    assert summary_pdf(with_quality).startswith(b"%PDF")
    assert summary_pdf({**base, "by_channel": {}}).startswith(b"%PDF")  # sin ventas


# ------------------------------------------------------------------------------ API

PDFS = ["/api/export/pnl.pdf", "/api/export/summary.pdf"]


@pytest.mark.asyncio
async def test_catalog_lists_every_export(env):
    client, _ = env
    r = await client.get("/api/export/catalog")
    assert r.status_code == 200
    items = {i["name"]: i for i in r.json()}
    assert set(EXPORTS) <= set(items) and {"pnl", "summary"} <= set(items)
    assert items["pricing_floor"]["params"] == ["channel", "account", "days"]
    assert items["pnl"]["formats"] == ["pdf"]


@pytest.mark.asyncio
async def test_unknown_export_is_404_and_bad_range_is_422(env):
    client, _ = env
    assert (await client.get("/api/export/inventada.csv")).status_code == 404
    assert (await client.get("/api/export/profit_by_product.csv?start=2026-02-01&end=2026-01-01")).status_code == 422
    assert (await client.get("/api/export/reorder.csv?days=3")).status_code == 422
    assert (await client.get("/api/export/pnl.pdf?channel=mercadolibre")).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(EXPORTS))
async def test_every_csv_export_works_on_empty_database(env, name):
    client, _ = env
    r = await client.get(f"/api/export/{name}.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert f'filename="{name}_' in r.headers["content-disposition"]
    table = parse_csv(r.content)
    assert len(table[0]) == len({h for h in table[0]})  # encabezados únicos
    assert all(h for h in table[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PDFS)
async def test_pdfs_work_on_empty_database(env, path):
    client, _ = env
    r = await client.get(path)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and ".pdf" in r.headers["content-disposition"]


@pytest.mark.asyncio
async def test_exports_with_data_and_formula_in_product_name(env):
    client, maker = env
    rows = [
        {"channel": "walmart", "account": "walmart_1", "date": TODAY - timedelta(days=i), "order_id": f"O{i}",
         "sku": "A", "product_name": "=HYPERLINK(\"http://x\")", "units": 1, "gross_sales": 30.0,
         "promo": 0.0, "platform_fees": 4.5, "shipping_cost": 5.0, "refund_amount": 0.0}
        for i in range(5)
    ]
    async with maker() as s:
        await import_orders(s, pl.DataFrame(rows))
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()

    by_product = parse_csv((await client.get("/api/export/profit_by_product.csv")).content)
    assert by_product[0][0] == "SKU" and by_product[1][0] == "A"
    gross = by_product[0].index("Ventas brutas")
    assert by_product[1][gross] == "150.00"

    by_channel = parse_csv((await client.get("/api/export/profit_by_channel.csv?channel=walmart")).content)
    assert by_channel[1][0] == "walmart"

    pricing = parse_csv((await client.get("/api/export/pricing_floor.csv")).content)
    name_col = pricing[0].index("Producto")
    assert pricing[1][name_col].startswith("'=HYPERLINK")

    pnl = parse_csv((await client.get("/api/export/pnl_monthly.csv")).content)
    assert pnl[0][:2] == ["Mes", "Canal"] and pnl[-1][1] == "TOTAL"

    for path in PDFS:
        assert (await client.get(path)).content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_summary_collects_quality_checks_when_module_exists(env):
    _, maker = env
    from app.pdf_reports import collect_summary

    async with maker() as s:
        data = await collect_summary(s, start=None, end=None, channel=None)
    assert isinstance(data["quality"], list) and len(data["quality"]) <= 10
    assert all(c["severity"] != "ok" for c in data["quality"])
    assert summary_pdf(data).startswith(b"%PDF")
