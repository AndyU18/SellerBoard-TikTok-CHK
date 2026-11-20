"""Datos SINTÉTICOS de demostración (no son datos reales de CHK).

Uso:  uv run python -m app.seed
Se niega a correr si la base ya tiene órdenes.
"""

from __future__ import annotations

import asyncio
import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.costs import set_product_cost
from app.db import SessionLocal, init_db
from app.importers.canonical import normalize_ad_spend, normalize_orders, read_table
from app.importers.persist import import_ad_spend, import_orders
from app.models import OrderLine, Product

# sku, nombre, precio por canal, COGS unitario
PRODUCTS = [
    ("GRN-100", "Molino de granos hierro 2400g", {"amazon": 74.99, "tiktok_shop": 59.99, "walmart": 72.99}, 21.69),
    ("GRN-101", "Molino de granos hierro 1200g", {"amazon": 65.99, "tiktok_shop": 49.99, "walmart": 64.99}, 19.81),
    ("TOR-003", "Prensa de tortillas 8in", {"amazon": 29.99, "tiktok_shop": 24.99, "walmart": 28.99}, 10.18),
    ("TOR-008", "Prensa de tortillas HD", {"amazon": 49.99, "tiktok_shop": 39.99, "walmart": 48.99}, 17.09),
]
# canal -> (cuenta, comisión %, envío por unidad, prob. de venta diaria, ads/día)
CHANNELS = {
    "amazon": [("amazon_1", 0.30, 0.0, 0.9, 25.0), ("amazon_2", 0.30, 0.0, 0.5, 12.0)],
    "tiktok_shop": [("tiktok_shop_1", 0.08, 7.5, 0.8, 40.0)],
    "walmart": [("walmart_1", 0.15, 4.0, 0.5, 8.0)],
}
DAYS = 90


def build_csvs(seed: int = 7) -> tuple[bytes, bytes]:
    rng = random.Random(seed)
    today = date.today()
    orders = ["date,channel,account,order_id,sku,product_name,units,sales,fees,shipping,refunds"]
    ads = ["date,channel,account,sku,spend"]
    n = 0
    for d in range(DAYS):
        day = (today - timedelta(days=DAYS - d)).isoformat()
        for channel, accounts in CHANNELS.items():
            for account, fee, ship, prob, ad_day in accounts:
                ads.append(f"{day},{channel},{account},,{round(ad_day * rng.uniform(0.7, 1.3), 2)}")
                for sku, name, prices, _ in PRODUCTS:
                    if rng.random() > prob:
                        continue
                    units = rng.randint(1, 4)
                    sales = round(prices[channel] * units, 2)
                    refund = sales if rng.random() < 0.04 else 0
                    n += 1
                    orders.append(
                        f"{day},{channel},{account},{channel[:2].upper()}-{n:06d},{sku},{name},"
                        f"{units},{sales},{round(sales * fee, 2)},{round(ship * units, 2)},{refund}"
                    )
    return "\n".join(orders).encode(), "\n".join(ads).encode()


async def main() -> None:
    await init_db()
    orders_csv, ads_csv = build_csvs()
    async with SessionLocal() as session:
        if await session.scalar(select(func.count()).select_from(OrderLine)):
            print("La base ya tiene órdenes; no se cargan datos demo.")
            return
        o = await import_orders(session, normalize_orders(read_table(orders_csv, "o.csv")))
        a = await import_ad_spend(session, normalize_ad_spend(read_table(ads_csv, "a.csv")))
        cogs = {sku: c for sku, _, _, c in PRODUCTS}
        for product in (await session.scalars(select(Product))).all():
            await set_product_cost(session, product.id, Decimal(str(cogs[product.sku])))
        await session.commit()
    print(f"Demo cargada: {o.inserted} líneas de orden, {a.inserted} filas de publicidad.")


if __name__ == "__main__":
    asyncio.run(main())
