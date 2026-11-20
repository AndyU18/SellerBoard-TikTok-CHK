"""Modelos SQLAlchemy 2.0 de la capa plata (esquemas sales / catalog).

La base de datos real (Supabase) la crean las migraciones de supabase/migrations/. Estos modelos
declaran solo las columnas que el backend lee o escribe; el resto tiene valores por defecto en la
base. En desarrollo y tests con SQLite, db.py quita los esquemas (schema_translate_map) y crea
las tablas a partir de estos modelos. Costos SIEMPRE positivos.
"""

from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(14, 4)
# En SQLite solo un INTEGER PRIMARY KEY se autoincrementa.
BigId = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Channel(str, enum.Enum):
    """Códigos válidos de sales.channel (se usa para validar parámetros de la API)."""

    amazon = "amazon"
    tiktok_shop = "tiktok_shop"
    walmart = "walmart"


class ChannelRow(Base):
    __tablename__ = "channel"
    __table_args__ = ({"schema": "sales"},)

    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)


class Account(Base):
    """Una cuenta de venta (p. ej. amazon_1, amazon_2, tiktok_shop_us, walmart_1)."""

    __tablename__ = "account"
    __table_args__ = (UniqueConstraint("channel_code", "name"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    channel_code: Mapped[str] = mapped_column(ForeignKey("sales.channel.code"))
    name: Mapped[str] = mapped_column(String)


class Product(Base):
    """Producto maestro. El SKU maestro une los listados de todos los canales.

    El costo (COGS) vive en ProductCostHistory, con fecha de vigencia.
    """

    __tablename__ = "product"
    __table_args__ = ({"schema": "catalog"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    sku: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str | None] = mapped_column(String)

    listings: Mapped[list[ChannelListing]] = relationship(back_populates="product")


class ChannelListing(Base):
    """SKU/ID del producto en una cuenta concreta (ASIN, ID TikTok Shop, ID Walmart)."""

    __tablename__ = "channel_listing"
    __table_args__ = (UniqueConstraint("account_id", "channel_sku"), {"schema": "catalog"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog.product.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    channel_sku: Mapped[str] = mapped_column(String)

    product: Mapped[Product] = relationship(back_populates="listings")


class CostComponentType(Base):
    __tablename__ = "cost_component_type"
    __table_args__ = ({"schema": "catalog"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)


class ProductCostHistory(Base):
    """Un componente del costo unitario con su fecha de vigencia (valid_from)."""

    __tablename__ = "product_cost_history"
    __table_args__ = (
        UniqueConstraint("product_id", "component_type_id", "valid_from"),
        {"schema": "catalog"},
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog.product.id"))
    component_type_id: Mapped[int] = mapped_column(ForeignKey("catalog.cost_component_type.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    valid_from: Mapped[date] = mapped_column(Date)


class SalesOrder(Base):
    """Cabecera de la orden. Una orden tiene una línea por producto."""

    __tablename__ = "sales_order"
    __table_args__ = (UniqueConstraint("account_id", "external_order_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_order_id: Mapped[str] = mapped_column(String)
    order_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String, default="imported")


class OrderLine(Base):
    """Un producto dentro de una orden. account_id y order_date se copian de la cabecera."""

    __tablename__ = "order_line"
    __table_args__ = (UniqueConstraint("order_id", "product_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sales.sales_order.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    order_date: Mapped[date] = mapped_column(Date, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog.product.id"))
    channel_sku: Mapped[str] = mapped_column(String)
    units: Mapped[int]
    gross_sales: Mapped[Decimal] = mapped_column(Money)
    promo: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    platform_fees: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    shipping_cost: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    refund_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))


class AdSpendDaily(Base):
    """Gasto publicitario diario (Amazon Ads, TikTok Ads, Walmart Connect)."""

    __tablename__ = "ad_spend_daily"
    __table_args__ = ({"schema": "sales"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("catalog.product.id"))
    spend_date: Mapped[date] = mapped_column(Date, index=True)
    spend: Mapped[Decimal] = mapped_column(Money)
