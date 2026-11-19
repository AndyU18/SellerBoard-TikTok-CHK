"""Modelos de base de datos (SQLAlchemy 2.0).

Un solo esquema para todos los canales. Montos de costo SIEMPRE positivos.
"""

from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(14, 2)


class Base(DeclarativeBase):
    pass


class Channel(str, enum.Enum):
    amazon = "amazon"
    tiktok_shop = "tiktok_shop"
    walmart = "walmart"


class Account(Base):
    """Una cuenta de venta (p. ej. amazon_1, amazon_2, tiktok_shop_1, walmart_1)."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("channel", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[Channel] = mapped_column(Enum(Channel, native_enum=False, length=20))
    name: Mapped[str] = mapped_column(String(80))


class Product(Base):
    """Producto maestro. El SKU maestro une los listados de todos los canales."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str | None] = mapped_column(String(300))
    cogs_unit: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal(0))

    listings: Mapped[list[ChannelListing]] = relationship(back_populates="product")


class ChannelListing(Base):
    """SKU/ID del producto en una cuenta concreta (ASIN, ID TikTok Shop, ID Walmart)."""

    __tablename__ = "channel_listings"
    __table_args__ = (UniqueConstraint("account_id", "channel_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    channel_sku: Mapped[str] = mapped_column(String(120))
    external_id: Mapped[str | None] = mapped_column(String(120))

    product: Mapped[Product] = relationship(back_populates="listings")


class OrderLine(Base):
    """Una línea de orden: un producto dentro de una orden."""

    __tablename__ = "order_lines"
    __table_args__ = (UniqueConstraint("account_id", "external_order_id", "product_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    external_order_id: Mapped[str] = mapped_column(String(80))
    order_date: Mapped[date] = mapped_column(Date, index=True)
    units: Mapped[int]
    gross_sales: Mapped[Decimal] = mapped_column(Money)
    promo: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    platform_fees: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    shipping_cost: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    refund_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))


class AdSpend(Base):
    """Gasto publicitario diario (Amazon Ads, TikTok Ads, Walmart Connect)."""

    __tablename__ = "ad_spend"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    spend_date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Money)


class Expense(Base):
    """Gasto indirecto (renta, software, sueldos...). Se puede asignar a cuenta/producto."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_date: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(80))
    amount: Mapped[Decimal] = mapped_column(Money)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
