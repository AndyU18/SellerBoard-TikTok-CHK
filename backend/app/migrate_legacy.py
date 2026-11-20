"""Migra una base SQLite del esquema antiguo (accounts/products/order_lines/ad_spend) al esquema
nuevo, usando los importadores idempotentes: se puede repetir sin duplicar.

Uso:  uv run python -m app.migrate_legacy chk_dev.db
Escribe en la base de CHK_DATABASE_URL (Supabase o el SQLite local). El archivo de origen
se abre en solo lectura.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import polars as pl

from app.db import SessionLocal, init_db
from app.importers.persist import import_ad_spend, import_cogs, import_orders

ORDERS_SQL = """
select ol.order_date as date, a.channel as channel, a.name as account,
       ol.external_order_id as order_id, p.sku as sku, p.name as product_name,
       ol.units as units, ol.gross_sales as gross_sales, ol.promo as promo,
       ol.platform_fees as platform_fees, ol.shipping_cost as shipping_cost,
       ol.refund_amount as refund_amount
from order_lines ol
join accounts a on a.id = ol.account_id
join products p on p.id = ol.product_id
"""
ADS_SQL = """
select s.spend_date as date, a.channel as channel, a.name as account, p.sku as sku,
       s.amount as amount
from ad_spend s
join accounts a on a.id = s.account_id
left join products p on p.id = s.product_id
"""
COGS_SQL = "select sku, name as product_name, cogs_unit from products where cogs_unit > 0"


def _query(con: sqlite3.Connection, sql: str) -> pl.DataFrame:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return pl.DataFrame(cur.fetchall(), schema=cols, orient="row", infer_schema_length=None)


def read_legacy(path: Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        orders = _query(con, ORDERS_SQL).with_columns(
            pl.col("date").cast(pl.Utf8).str.slice(0, 10).str.to_date("%Y-%m-%d"),
            pl.col("units").cast(pl.Int64),
            *[
                pl.col(c).cast(pl.Float64)
                for c in ("gross_sales", "promo", "platform_fees", "shipping_cost", "refund_amount")
            ],
        )
        ads = _query(con, ADS_SQL).with_columns(
            pl.col("date").cast(pl.Utf8).str.slice(0, 10).str.to_date("%Y-%m-%d"),
            pl.col("amount").cast(pl.Float64),
        )
        cogs = _query(con, COGS_SQL).with_columns(
            pl.col("cogs_unit").cast(pl.Float64),
            pl.lit(None, dtype=pl.Date).alias("valid_from"),
        )
    finally:
        con.close()
    return orders, ads, cogs


async def main(path: Path) -> None:
    orders, ads, cogs = read_legacy(path)
    await init_db()
    async with SessionLocal() as session:
        o = await import_orders(session, orders) if orders.height else None
        a = await import_ad_spend(session, ads) if ads.height else None
        c = await import_cogs(session, cogs) if cogs.height else None
        await session.commit()
    print(f"Órdenes: {o}\nPublicidad: {a}\nCostos: {c}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        sys.exit("Uso: python -m app.migrate_legacy <archivo_sqlite_antiguo>")
    asyncio.run(main(Path(sys.argv[1])))
