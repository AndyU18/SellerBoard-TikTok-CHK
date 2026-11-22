"""Modelos SQLAlchemy 2.0 de la capa plata (esquemas sales / catalog).

La base de datos real (Supabase) la crean las migraciones de supabase/migrations/. Estos modelos
declaran solo las columnas que el backend lee o escribe; el resto tiene valores por defecto en la
base. En desarrollo y tests con SQLite, db.py quita los esquemas (schema_translate_map) y crea
las tablas a partir de estos modelos. Costos SIEMPRE positivos.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
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
    shopify = "shopify"


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
    __table_args__ = (
        UniqueConstraint("account_id", "channel_sku"),
        {"schema": "catalog"},
    )

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
    component_type_id: Mapped[int] = mapped_column(
        ForeignKey("catalog.cost_component_type.id")
    )
    amount: Mapped[Decimal] = mapped_column(Money)
    valid_from: Mapped[date] = mapped_column(Date)


class SalesOrder(Base):
    """Cabecera de la orden. Una orden tiene una línea por producto."""

    __tablename__ = "sales_order"
    __table_args__ = (
        UniqueConstraint("account_id", "external_order_id"),
        {"schema": "sales"},
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_order_id: Mapped[str] = mapped_column(String)
    order_date: Mapped[date] = mapped_column(Date)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Subsidio de la plataforma (no resta utilidad) y venta a precio de lista (0 = desconocida).
    platform_discount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    list_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))


class Settlement(Base):
    """Liquidación / pago de la plataforma (TikTok: estado de cuenta). Alimenta el flujo de caja."""

    __tablename__ = "settlement"
    __table_args__ = (UniqueConstraint("account_id", "external_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_id: Mapped[str] = mapped_column(String)
    statement_date: Mapped[date | None] = mapped_column(Date)
    payout_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String)
    gross_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    fees_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    shipping_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    adjustment_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    net_payout: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))


class ReturnCase(Base):
    """Devolución o cancelación de la plataforma (RMA). Ver ..._returns_and_shipments.sql."""

    __tablename__ = "return_case"
    __table_args__ = (UniqueConstraint("account_id", "external_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # return | cancellation
    case_type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)
    reason_code: Mapped[str | None] = mapped_column(String)
    initiated_by: Mapped[str | None] = mapped_column(String)
    external_order_id: Mapped[str] = mapped_column(String, index=True)
    case_date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refund_total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))


class ReturnCaseLine(Base):
    __tablename__ = "return_case_line"
    __table_args__ = ({"schema": "sales"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("sales.return_case.id"), index=True)
    sku: Mapped[str] = mapped_column(String)
    product_name: Mapped[str | None] = mapped_column(String)
    units: Mapped[int] = mapped_column(default=1)
    refund_total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))


class Shipment(Base):
    """Tiempos de una orden (pago, listo para enviar, recolección, entrega, fecha límite)."""

    __tablename__ = "shipment"
    __table_args__ = (UniqueConstraint("account_id", "external_order_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_order_id: Mapped[str] = mapped_column(String)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str | None] = mapped_column(String)
    shipping_type: Mapped[str | None] = mapped_column(String)
    fulfillment_type: Mapped[str | None] = mapped_column(String)
    delivery_option: Mapped[str | None] = mapped_column(String)
    carrier: Mapped[str | None] = mapped_column(String)
    tracking_number: Mapped[str | None] = mapped_column(String)
    is_sample: Mapped[bool] = mapped_column(default=False)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SampleApplication(Base):
    """Solicitud de muestra de un creador (embudo). Ver ..._creator_samples.sql."""

    __tablename__ = "sample_application"
    __table_args__ = (UniqueConstraint("account_id", "external_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_id: Mapped[str] = mapped_column(String)
    creator_username: Mapped[str | None] = mapped_column(String, index=True)
    follower_count: Mapped[int | None]
    creator_gmv: Mapped[Decimal | None] = mapped_column(Money)
    status: Mapped[str | None] = mapped_column(String)
    external_order_id: Mapped[str | None] = mapped_column(String, index=True)
    product_title: Mapped[str | None] = mapped_column(String)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class SampleLine(Base):
    """Producto enviado en una orden de muestra (para costearla aunque nunca se liquide)."""

    __tablename__ = "sample_line"
    __table_args__ = (
        UniqueConstraint("account_id", "external_order_id", "sku"),
        {"schema": "sales"},
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_order_id: Mapped[str] = mapped_column(String)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    sku: Mapped[str] = mapped_column(String)
    product_name: Mapped[str | None] = mapped_column(String)
    units: Mapped[int] = mapped_column(default=1)


class AffiliateOrder(Base):
    """Venta atribuida a un creador. La comisión ya está en platform_fees de la utilidad."""

    __tablename__ = "affiliate_order"
    __table_args__ = (
        UniqueConstraint("account_id", "external_order_id", "sku_id"),
        {"schema": "sales"},
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_order_id: Mapped[str] = mapped_column(String, index=True)
    sku_id: Mapped[str] = mapped_column(String)
    order_date: Mapped[date] = mapped_column(Date, index=True)
    creator_username: Mapped[str | None] = mapped_column(String, index=True)
    content_type: Mapped[str | None] = mapped_column(String)
    content_id: Mapped[str | None] = mapped_column(String)
    sku: Mapped[str | None] = mapped_column(String)
    units: Mapped[int] = mapped_column(default=1)
    commission_base: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    commission: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    settlement_status: Mapped[str | None] = mapped_column(String)


class TrafficDaily(Base):
    """Embudo de tráfico por día y origen (ALL | LIVE | VIDEO | PRODUCT_CARD)."""

    __tablename__ = "traffic_daily"
    __table_args__ = (UniqueConstraint("account_id", "day", "source"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String)
    impressions: Mapped[int | None]
    page_views: Mapped[int | None]
    visitors: Mapped[int | None]
    buyers: Mapped[int | None]
    orders: Mapped[int | None]
    units: Mapped[int | None]
    gmv: Mapped[Decimal | None] = mapped_column(Money)


class SyncRun(Base):
    """Una ejecución de sincronización (manual o programada). Tabla ops.sync_run."""

    __tablename__ = "sync_run"
    __table_args__ = ({"schema": "ops"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    job_type: Mapped[str] = mapped_column(String)  # tiktok_shop | walmart | redo
    triggered_by: Mapped[str] = mapped_column(String, default="schedule")  # schedule | manual | import
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, default="running")  # running | success | partial | failed
    error: Mapped[str | None] = mapped_column(String)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


class SellerPerformance(Base):
    """Foto diaria de los indicadores del vendedor por ventana (7/14/30/60/90 días), en %."""

    __tablename__ = "seller_performance"
    __table_args__ = (
        UniqueConstraint("account_id", "snapshot_date", "window_days"),
        {"schema": "sales"},
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    window_days: Mapped[int]
    ots: Mapped[Decimal | None] = mapped_column(Money)
    otd: Mapped[Decimal | None] = mapped_column(Money)
    vtr: Mapped[Decimal | None] = mapped_column(Money)
    refund_rate_seller_fault: Mapped[Decimal | None] = mapped_column(Money)
    refund_rate_customer_fault: Mapped[Decimal | None] = mapped_column(Money)
    cancellation_seller_fault: Mapped[Decimal | None] = mapped_column(Money)
    cancellation_customer_fault: Mapped[Decimal | None] = mapped_column(Money)


class ListingStock(Base):
    """Estado actual de un SKU publicado: precio, publicación y existencias."""

    __tablename__ = "listing_stock"
    __table_args__ = (UniqueConstraint("account_id", "sku"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    sku: Mapped[str] = mapped_column(String)
    product_name: Mapped[str | None] = mapped_column(String)
    price: Mapped[Decimal | None] = mapped_column(Money)
    availability: Mapped[str | None] = mapped_column(String)
    published_status: Mapped[str | None] = mapped_column(String)
    lifecycle_status: Mapped[str | None] = mapped_column(String)
    unpublished_reasons: Mapped[str | None] = mapped_column(String)
    avail_to_sell: Mapped[int | None]
    reserved: Mapped[int | None]
    input_qty: Mapped[int | None]


class LiveSession(Base):
    """Transmisión LIVE (propia o de un creador) con sus ventas."""

    __tablename__ = "live_session"
    __table_args__ = (UniqueConstraint("account_id", "external_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    external_id: Mapped[str] = mapped_column(String)
    username: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gmv: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    gmv_24h: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    orders: Mapped[int] = mapped_column(default=0)
    customers: Mapped[int] = mapped_column(default=0)
    units: Mapped[int] = mapped_column(default=0)
    products_added: Mapped[int] = mapped_column(default=0)
    products_sold: Mapped[int] = mapped_column(default=0)
    click_to_order_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    avg_price: Mapped[Decimal | None] = mapped_column(Money)


class VideoPerformance(Base):
    """Vistas, CTR y ventas de un video en la ventana sincronizada (máx. 60 días)."""

    __tablename__ = "video_performance"
    __table_args__ = (UniqueConstraint("account_id", "video_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    video_id: Mapped[str] = mapped_column(String)
    username: Mapped[str | None] = mapped_column(String, index=True)
    title: Mapped[str | None] = mapped_column(String)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    views: Mapped[int | None]
    click_through_rate: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    gmv: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    orders: Mapped[int] = mapped_column(default=0)
    units: Mapped[int] = mapped_column(default=0)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)


class AdSpendDaily(Base):
    """Gasto publicitario diario (Amazon Ads, TikTok Ads, Walmart Connect)."""

    __tablename__ = "ad_spend_daily"
    __table_args__ = ({"schema": "sales"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("catalog.product.id"))
    spend_date: Mapped[date] = mapped_column(Date, index=True)
    spend: Mapped[Decimal] = mapped_column(Money)


class GoldDailyProfit(Base):
    """Capa oro: utilidad por día, cuenta y producto. El dashboard lee SOLO de aquí.

    net_profit es una columna generada por la base (misma fórmula que profit.py), así la tabla
    nunca queda inconsistente. product_id nulo = publicidad sin SKU asignado.
    """

    __tablename__ = "daily_profit"
    __table_args__ = ({"schema": "gold"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    profit_date: Mapped[date] = mapped_column(Date, index=True)
    channel_code: Mapped[str] = mapped_column(ForeignKey("sales.channel.code"))
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("catalog.product.id"))
    orders: Mapped[int] = mapped_column(default=0)
    units: Mapped[int] = mapped_column(default=0)
    gross_sales: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    promo: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    platform_fees: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    shipping_cost: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    refund_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    ad_spend: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    cogs: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    allocated_expenses: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    net_profit: Mapped[Decimal] = mapped_column(
        Money,
        Computed(
            "gross_sales - promo - platform_fees - shipping_cost - refund_amount"
            " - ad_spend - cogs - allocated_expenses",
            persisted=True,
        ),
    )
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoldRefreshLog(Base):
    """Bitácora de refrescos de la capa oro."""

    __tablename__ = "refresh_log"
    __table_args__ = ({"schema": "gold"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    rows_written: Mapped[int] = mapped_column(default=0)
    reason: Mapped[str | None] = mapped_column(String)


# ---------------------------------------------------------------- esquema app (usuarios y permisos)
class Profile(Base):
    """Perfil de un usuario de Supabase Auth (lo crea un trigger al registrarse)."""

    __tablename__ = "profile"
    __table_args__ = ({"schema": "app"},)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str | None] = mapped_column(String)
    full_name: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(default=True)


class Role(Base):
    __tablename__ = "role"
    __table_args__ = ({"schema": "app"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)


class Permission(Base):
    __tablename__ = "permission"
    __table_args__ = ({"schema": "app"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = ({"schema": "app"},)

    role_id: Mapped[int] = mapped_column(ForeignKey("app.role.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("app.permission.id"), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = ({"schema": "app"},)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app.profile.user_id"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("app.role.id"), primary_key=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ShippingLabelCost(Base):
    """Costo de etiquetas por orden (Redo...). Ver supabase/migrations/..._shipping_label_cost.sql."""

    __tablename__ = "shipping_label_cost"
    __table_args__ = (UniqueConstraint("source", "external_order_id"), {"schema": "sales"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    source: Mapped[str] = mapped_column(String)
    external_order_id: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    charges: Mapped[int] = mapped_column(default=1)
    first_charged: Mapped[date | None] = mapped_column(Date)
    last_charged: Mapped[date | None] = mapped_column(Date)
