import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.importers.canonical import ImportValidationError, read_table
from app.importers.redo import normalize_redo
from app.main import app

HEADER = "Created at,Period,Type,Category,Order,Amount,Invoice,Currency\n"
REDO = (
    HEADER
    # orden con varios cargos y reversiones: neto 10.41 + 3.05 + 0.5 - 0.5 = 13.46
    + '"Nov 17, 2026","p",Charge,Shipping Labels,T-100,10.41,i,USD\n'
    + '"Nov 17, 2026","p",Charge,Shipping Labels,T-100,3.05,i,USD\n'
    + '"Nov 18, 2026","p",Charge,Shipping Labels,T-100,0.5,i,USD\n'
    + '"Nov 18, 2026","p",Charge,Shipping Labels,T-100,-0.5,i,USD\n'
    # orden de dos productos
    + '"Nov 17, 2026","p",Charge,Shipping Labels,T-200,12.00,i,USD\n'
    # orden que todavía no existe en la base (otro canal o sin sincronizar)
    + '"Nov 17, 2026","p",Charge,Shipping Labels,#2599,9.20,i,USD\n'
    # cargo sin orden (cuota) y recarga de saldo: no son costo de una orden
    + '"Nov 17, 2026","p",Charge,Shipping Labels,,30,i,USD\n'
    + '"Nov 17, 2026",,Transaction,,,1030,i,USD\n'
).encode()

ORDERS = (
    b"date,channel,account,order_id,sku,units,sales\n"
    b"2026-11-17,tiktok_shop,tt1,T-100,A,1,100\n"
    b"2026-11-17,tiktok_shop,tt1,T-200,A,1,30\n"
    b"2026-11-17,tiktok_shop,tt1,T-200,B,1,70\n"
)


def test_normalize_sums_per_order_and_reports_what_it_skips():
    df, stats = normalize_redo(read_table(REDO, "billing.csv"))
    per = {r["external_order_id"]: r for r in df.to_dicts()}
    assert per["T-100"]["amount"] == pytest.approx(13.46) and per["T-100"]["charges"] == 4
    assert per["T-200"]["amount"] == 12.0 and "#2599" in per
    assert stats.transactions_excluded == 1  # la recarga no es costo
    assert stats.unassigned_charges == 1 and stats.unassigned_total == 30.0
    assert stats.orders == 3


def test_wrong_file_is_rejected_with_a_clear_message():
    with pytest.raises(ImportValidationError) as exc:
        normalize_redo(read_table(b"a,b\n1,2\n", "x.csv"))
    assert "Created At" in exc.value.problems[0] or "billing-transactions" in exc.value.problems[0]


def test_bad_date_is_reported_by_row():
    bad = (HEADER + 'ayer,p,Charge,Shipping Labels,T-1,5,i,USD\n').encode()
    with pytest.raises(ImportValidationError) as exc:
        normalize_redo(read_table(bad, "x.csv"))
    assert "filas [2]" in exc.value.problems[0]


@pytest_asyncio.fixture
async def client():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _post(client, path, data):
    r = await client.post(path, files={"file": ("f.csv", data, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_label_cost_is_split_across_products_and_lowers_profit(client):
    await _post(client, "/api/import/orders", ORDERS)
    out = await _post(client, "/api/import/shipping-labels", REDO)
    assert out["inserted"] == 3 and out["matched_orders"] == 2 and out["unmatched_orders"] == 1
    assert out["stats"]["unassigned_total"] == 30.0

    rows = {r["sku"]: r for r in (await client.get("/api/profit?group_by=product")).json()["rows"]}
    # T-100 (13.46) solo tiene A. T-200 (12.00) se reparte 30/70 entre A y B.
    assert rows["A"]["shipping_cost"] == pytest.approx(13.46 + 12 * 0.3)
    assert rows["B"]["shipping_cost"] == pytest.approx(12 * 0.7)
    totals = (await client.get("/api/profit")).json()["totals"]
    assert totals["shipping_cost"] == pytest.approx(13.46 + 12.0)  # la orden #2599 aún no cuenta


@pytest.mark.asyncio
async def test_reimport_is_idempotent_and_order_arriving_later_picks_up_its_label(client):
    await _post(client, "/api/import/shipping-labels", REDO)  # antes de que existan las órdenes
    again = await _post(client, "/api/import/shipping-labels", REDO)
    assert again["inserted"] == 0 and again["matched_orders"] == 0

    await _post(client, "/api/import/orders", ORDERS)  # llegan las órdenes
    await _post(client, "/api/gold/refresh", b"")  # el refresco completo recoge las etiquetas guardadas
    totals = (await client.get("/api/profit")).json()["totals"]
    assert totals["shipping_cost"] == pytest.approx(25.46)
