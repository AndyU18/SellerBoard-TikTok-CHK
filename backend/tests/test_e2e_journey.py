"""Recorrido de extremo a extremo por HTTP con la aplicación real (app.main) y una base en memoria.

Simula lo que haría una persona: sube órdenes y costos, ve utilidad y alertas, detecta un SKU duplicado y
lo fusiona, vuelve a importar con el SKU viejo (no debe crear otro producto) y consulta precios,
explicación de la utilidad y el resumen diario. No toca ninguna plataforma ni envía nada.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.main import app

TODAY = datetime.now(UTC).date()
HEADER = (
    "date,channel,account,order_id,sku,product_name,units,gross_sales,promo,platform_fees,shipping_cost,refund_amount\n"
)


def orders_csv(prefix: str, sku: str, days: int, price: float = 40.0) -> bytes:
    rows = [
        f"{TODAY - timedelta(days=i)},walmart,walmart_1,{prefix}{i},{sku},Santa Mariachi,1,{price},0,{price * 0.15},5,0"
        for i in range(days)
    ]
    return (HEADER + "\n".join(rows) + "\n").encode()


def upload(name: str, content: bytes) -> dict:
    return {"file": (name, content, "text/csv")}


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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def product_skus(client) -> set[str]:
    return {p["sku"] for p in (await client.get("/api/products")).json()}


@pytest.mark.asyncio
async def test_full_user_journey(client):
    # 1. Importar órdenes del mismo producto con dos SKU (uno mal capturado) y el costo de uno solo.
    first = await client.post("/api/import/orders", files=upload("a.csv", orders_csv("A", "XMAS-8027", 6)))
    assert first.status_code == 200, first.text
    second = await client.post("/api/import/orders", files=upload("b.csv", orders_csv("B", "XMAS8027", 4)))
    assert second.status_code == 200, second.text
    cogs = await client.post("/api/import/cogs", files=upload("c.csv", b"sku,cogs_unit\nXMAS-8027,12.50\n"))
    assert cogs.status_code == 200, cogs.text
    assert await product_skus(client) == {"XMAS-8027", "XMAS8027"}

    # 2. La utilidad ya se ve, y las alertas avisan que falta el costo de uno de los dos.
    profit = (await client.get("/api/profit", params={"group_by": "product"})).json()
    assert {r["sku"] for r in profit["rows"]} == {"XMAS-8027", "XMAS8027"}
    alerts = (await client.get("/api/alerts")).json()
    assert any(a["id"] == "cogs:missing" for a in alerts["alerts"])

    # 3. Calidad de datos: hay un duplicado y baja la puntuación.
    quality = (await client.get("/api/data-quality")).json()
    dup = next(c for c in quality["checks"] if c["id"] == "duplicate_skus")
    assert dup["count"] >= 1 and quality["score"] < 100

    # 4. El gestor lo detecta y lo fusiona (irreversible, valida entradas).
    groups = (await client.get("/api/skus/duplicates")).json()["groups"]
    assert len(groups) == 1
    bad = await client.post("/api/skus/merge", json={"source_sku": "XMAS8027", "target_sku": "XMAS8027"})
    assert bad.status_code == 422
    merged = await client.post(
        "/api/skus/merge", json={"source_sku": "XMAS8027", "target_sku": "XMAS-8027", "reason": "error de captura"}
    )
    assert merged.status_code == 200, merged.text
    assert await product_skus(client) == {"XMAS-8027"}
    (row,) = (await client.get("/api/profit", params={"group_by": "product"})).json()["rows"]
    assert row["units"] == 10 and row["cogs"] == pytest.approx(125.0)  # las 10 unidades ya tienen costo
    assert (await client.get("/api/skus/duplicates")).json()["groups"] == []
    assert [a["alias"] for a in (await client.get("/api/skus/aliases")).json()] == ["XMAS8027"]

    # 5. Una importación posterior con el SKU viejo NO crea otro producto: se asigna al maestro.
    again = await client.post("/api/import/orders", files=upload("d.csv", orders_csv("C", "XMAS8027", 2)))
    assert again.status_code == 200, again.text
    assert await product_skus(client) == {"XMAS-8027"}
    (row,) = (await client.get("/api/profit", params={"group_by": "product"})).json()["rows"]
    assert row["units"] == 12

    # 6. Reportes que usan todo lo anterior responden y son coherentes.
    floor = (await client.get("/api/pricing/floor", params={"days": 30})).json()
    assert [r["sku"] for r in floor["rows"]] == ["XMAS-8027"]
    assert floor["rows"][0]["break_even_price"] > 12.5
    explain = await client.get("/api/profit/explain", params={"days": 7})
    assert explain.status_code == 200 and explain.json()["headline"]
    preview = (await client.get("/api/digest/preview", params={"period": "daily"})).json()
    assert preview["text"] and "<script" not in preview["html"].lower()

    # 7. Lo que sale de la aplicación está apagado por defecto: sin canales no se envía nada.
    status = (await client.get("/api/notifications/status")).json()
    assert status["enabled"] is False and not any(status["channels"].values())
    assert (await client.post("/api/notifications/test")).status_code == 409
    assistant = (await client.get("/api/assistant/status")).json()
    assert assistant["configured"] is False
    ask = await client.post("/api/assistant/ask", json={"question": "¿Por qué bajó la utilidad?", "days": 7})
    assert ask.status_code == 503  # sin clave no se manda nada a ningún servicio externo
