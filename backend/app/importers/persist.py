"""Guarda datos normalizados en la base de datos de forma idempotente.

- Órdenes: clave única (cuenta, orden) en la cabecera y (orden, producto) en la línea.
  Reimportar actualiza, no duplica.
- Publicidad: se reemplaza el rango de fechas de cada cuenta presente en el archivo.
- Costos: cada cambio es una fila con vigencia en catalog.product_cost_history.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import polars as pl
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import set_product_cost
from app.models import (
    Account,
    AdSpendDaily,
    ChannelListing,
    OrderLine,
    Product,
    SalesOrder,
)


@dataclass
class ImportResult:
    inserted: int = 0
    updated: int = 0
    accounts_created: int = 0
    products_created: int = 0


def _dec(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


class _Resolver:
    """Resuelve cuentas, productos y cabeceras de orden con caché, creándolos si no existen."""

    def __init__(self, session: AsyncSession, result: ImportResult):
        self.session = session
        self.result = result
        self._accounts: dict[tuple[str, str], Account] = {}
        self._products: dict[tuple[int, str], int] = {}
        self._orders: dict[tuple[int, str], SalesOrder] = {}

    async def account(self, channel: str, name: str) -> Account:
        key = (channel, name)
        if key not in self._accounts:
            acc = await self.session.scalar(
                select(Account).where(Account.channel_code == channel, Account.name == name)
            )
            if acc is None:
                acc = Account(channel_code=channel, name=name)
                self.session.add(acc)
                await self.session.flush()
                self.result.accounts_created += 1
            self._accounts[key] = acc
        return self._accounts[key]

    async def product_id(self, account: Account, sku: str, name: str | None) -> int:
        key = (account.id, sku)
        if key in self._products:
            return self._products[key]
        listing = await self.session.scalar(
            select(ChannelListing).where(
                ChannelListing.account_id == account.id, ChannelListing.channel_sku == sku
            )
        )
        if listing is not None:
            pid = listing.product_id
        else:
            product = await self.session.scalar(select(Product).where(Product.sku == sku))
            if product is None:
                product = Product(sku=sku, name=name)
                self.session.add(product)
                await self.session.flush()
                self.result.products_created += 1
            elif name and not product.name:
                product.name = name
            self.session.add(
                ChannelListing(product_id=product.id, account_id=account.id, channel_sku=sku)
            )
            await self.session.flush()
            pid = product.id
        self._products[key] = pid
        return pid

    async def order(self, account: Account, external_id: str, day) -> SalesOrder:
        key = (account.id, external_id)
        if key not in self._orders:
            order = await self.session.scalar(
                select(SalesOrder).where(
                    SalesOrder.account_id == account.id,
                    SalesOrder.external_order_id == external_id,
                )
            )
            if order is None:
                order = SalesOrder(
                    account_id=account.id, external_order_id=external_id, order_date=day
                )
                self.session.add(order)
                await self.session.flush()
            else:
                order.order_date = day
            self._orders[key] = order
        return self._orders[key]


async def import_cogs(session: AsyncSession, df: pl.DataFrame) -> ImportResult:
    """`df` viene de `normalize_cogs`. Fija el costo; crea el producto si aún no existe."""
    result = ImportResult()
    products = {p.sku: p for p in (await session.scalars(select(Product))).all()}
    for row in df.iter_rows(named=True):
        product = products.get(row["sku"])
        created = product is None
        if created:
            product = Product(sku=row["sku"], name=row["product_name"])
            session.add(product)
            await session.flush()
            products[row["sku"]] = product
            result.products_created += 1
        elif row["product_name"] and not product.name:
            product.name = row["product_name"]
        changed = await set_product_cost(
            session, product.id, _dec(row["cogs_unit"]), row.get("valid_from")
        )
        if created:
            result.inserted += 1
        elif changed:
            result.updated += 1
    await session.flush()
    return result


async def import_orders(session: AsyncSession, df: pl.DataFrame) -> ImportResult:
    """`df` debe venir de `normalize_orders`."""
    result = ImportResult()
    resolver = _Resolver(session, result)

    # Una misma orden+SKU repetida en el archivo se suma en una sola línea.
    lines = df.group_by(["channel", "account", "order_id", "sku"], maintain_order=True).agg(
        pl.col("date").min(),
        pl.col("product_name").drop_nulls().first(),
        pl.col("units").sum(),
        pl.col("gross_sales").sum(),
        pl.col("promo").sum(),
        pl.col("platform_fees").sum(),
        pl.col("shipping_cost").sum(),
        pl.col("refund_amount").sum(),
    )

    for row in lines.iter_rows(named=True):
        account = await resolver.account(row["channel"], row["account"])
        product_id = await resolver.product_id(account, row["sku"], row["product_name"])
        order = await resolver.order(account, row["order_id"], row["date"])
        existing = await session.scalar(
            select(OrderLine).where(
                OrderLine.order_id == order.id, OrderLine.product_id == product_id
            )
        )
        values = {
            "order_date": row["date"],
            "channel_sku": row["sku"],
            "units": int(row["units"]),
            "gross_sales": _dec(row["gross_sales"]),
            "promo": _dec(row["promo"]),
            "platform_fees": _dec(row["platform_fees"]),
            "shipping_cost": _dec(row["shipping_cost"]),
            "refund_amount": _dec(row["refund_amount"]),
        }
        if existing is None:
            session.add(
                OrderLine(
                    order_id=order.id, account_id=account.id, product_id=product_id, **values
                )
            )
            result.inserted += 1
        else:
            for k, v in values.items():
                setattr(existing, k, v)
            result.updated += 1

    await session.flush()
    return result


async def import_ad_spend(session: AsyncSession, df: pl.DataFrame) -> ImportResult:
    """`df` debe venir de `normalize_ad_spend`. Reemplaza el rango de cada cuenta."""
    result = ImportResult()
    resolver = _Resolver(session, result)

    ranges = df.group_by(["channel", "account"]).agg(
        pl.col("date").min().alias("start"), pl.col("date").max().alias("end")
    )
    for r in ranges.iter_rows(named=True):
        account = await resolver.account(r["channel"], r["account"])
        await session.execute(
            delete(AdSpendDaily).where(
                AdSpendDaily.account_id == account.id,
                AdSpendDaily.spend_date >= r["start"],
                AdSpendDaily.spend_date <= r["end"],
            )
        )

    # La tabla admite una fila por cuenta, día y SKU: las repetidas del archivo se suman.
    daily = df.group_by(["channel", "account", "date", "sku"], maintain_order=True).agg(
        pl.col("amount").sum()
    )
    for row in daily.iter_rows(named=True):
        account = await resolver.account(row["channel"], row["account"])
        product_id = await resolver.product_id(account, row["sku"], None) if row["sku"] else None
        session.add(
            AdSpendDaily(
                account_id=account.id,
                product_id=product_id,
                spend_date=row["date"],
                spend=_dec(row["amount"]),
            )
        )
        result.inserted += 1

    await session.flush()
    return result
