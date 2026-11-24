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
    Boolean,
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
    # Estado de destino (p. ej. "Florida"). Solo el estado: nunca se guarda la dirección.
    ship_state: Mapped[str | None] = mapped_column(String)


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


class SettlementFee(Base):
    """Un concepto de una transacción de liquidación (comisión, descuento, envío...). Montos firmados."""

    __tablename__ = "settlement_fee"
    __table_args__ = (
        UniqueConstraint("account_id", "statement_id", "transaction_id", "concept"),
        {"schema": "sales"},
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("sales.settlement.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sales.account.id"))
    statement_id: Mapped[str] = mapped_column(String)
    transaction_id: Mapped[str] = mapped_column(String)
    external_order_id: Mapped[str | None] = mapped_column(String, index=True)
    concept: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)  # revenue | fee | shipping | adjustment | memo
    amount: Mapped[Decimal] = mapped_column(Money)
    fee_date: Mapped[date | None] = mapped_column(Date)


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


class SkuAlias(Base):
    """SKU que llega de una plataforma y el producto maestro al que pertenece (catalog.sku_alias)."""

    __tablename__ = "sku_alias"
    __table_args__ = ({"schema": "catalog"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    alias: Mapped[str] = mapped_column(String, unique=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog.product.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class NotificationLog(Base):
    """Un intento de envío del resumen (diario, semanal o de prueba) por un canal (ops.notification_log)."""

    __tablename__ = "notification_log"
    __table_args__ = ({"schema": "ops"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    kind: Mapped[str] = mapped_column(String)  # daily | weekly | test
    channel: Mapped[str] = mapped_column(String)  # email | slack | webhook
    status: Mapped[str] = mapped_column(String)  # sent | failed | skipped
    detail: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ExpenseCategory(Base):
    """Categoría de gasto indirecto (software, nómina, bodega...). Tabla finance.expense_category."""

    __tablename__ = "expense_category"
    __table_args__ = ({"schema": "finance"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(default=True)


class Expense(Base):
    """Gasto indirecto, puntual o recurrente. El estado de resultados lo prorratea por mes."""

    __tablename__ = "expense"
    __table_args__ = ({"schema": "finance"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("finance.expense_category.id"))
    description: Mapped[str] = mapped_column(String)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String, default="USD")
    recurrence: Mapped[str] = mapped_column(String, default="one_time")  # one_time | monthly | annual
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    channel_code: Mapped[str | None] = mapped_column(ForeignKey("sales.channel.code"))
    is_active: Mapped[bool] = mapped_column(default=True)


class InboundReceipt(Base):
    """Recepción de un contenedor: costo de fábrica, aranceles y flete por unidad de un SKU."""

    __tablename__ = "inbound_receipt"
    __table_args__ = (UniqueConstraint("container", "po", "sku"), {"schema": "ops"})

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    source: Mapped[str] = mapped_column(String, default="landed_cost_report")
    container: Mapped[str] = mapped_column(String)
    received_date: Mapped[date] = mapped_column(Date, index=True)
    po: Mapped[str] = mapped_column(String, default="")
    sku: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String)
    qty: Mapped[int]
    fob_unit: Mapped[Decimal] = mapped_column(Money)
    duty_unit: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    freight_unit: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))


class Goal(Base):
    """Meta mensual (ventas, utilidad o unidades), global o de un canal."""

    __tablename__ = "goal"
    __table_args__ = ({"schema": "finance"},)

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    month: Mapped[date] = mapped_column(Date, index=True)
    metric: Mapped[str] = mapped_column(String)  # gross_sales | net_profit | units
    channel_code: Mapped[str | None] = mapped_column(ForeignKey("sales.channel.code"))
    target: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class ActionStatus(Base):
    """Estado que el usuario le da a una acción del Centro de acciones (ops.action_status)."""

    __tablename__ = "action_status"
    __table_args__ = ({"schema": "ops"},)

    item_key: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)  # done | dismissed | snoozed
    note: Mapped[str | None] = mapped_column(String)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_by_email: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


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


class RedoCharge(Base):
    """Un cargo del libro de Redo (etiqueta, corrección, anulación, comisión). Tabla ops.redo_charge."""

    __tablename__ = "redo_charge"
    __table_args__ = ({"schema": "ops"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_name: Mapped[str | None] = mapped_column(String, index=True)
    subtype: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str | None] = mapped_column(String)
    product: Mapped[str | None] = mapped_column(String)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String, default="USD")
    note: Mapped[str | None] = mapped_column(String)
    fulfillment_group_id: Mapped[str | None] = mapped_column(String)
    shipment_id: Mapped[str | None] = mapped_column(String)
    reverses_charge_id: Mapped[str | None] = mapped_column(String)
    order_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RedoBill(Base):
    """Factura mensual de Redo con saldo de apertura y cierre. Tabla ops.redo_bill."""

    __tablename__ = "redo_bill"
    __table_args__ = ({"schema": "ops"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    charge_total: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    opening_balance: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    closing_balance: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    billed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RedoTopup(Base):
    """Recarga de saldo en Redo (financiamiento, no costo). Tabla ops.redo_topup."""

    __tablename__ = "redo_topup"
    __table_args__ = ({"schema": "ops"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str | None] = mapped_column(String)
    failure_message: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WalmartListingQuality(Base):
    """Foto diaria del puntaje de calidad de los listados de Walmart. Tabla ops.walmart_listing_quality."""

    __tablename__ = "walmart_listing_quality"
    __table_args__ = ({"schema": "ops"},)

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    listing_quality: Mapped[Decimal | None] = mapped_column(Money)
    offer_score: Mapped[Decimal | None] = mapped_column(Money)
    rating_review_score: Mapped[Decimal | None] = mapped_column(Money)
    content_score: Mapped[Decimal | None] = mapped_column(Money)
    price_score: Mapped[Decimal | None] = mapped_column(Money)
    shipping_score: Mapped[Decimal | None] = mapped_column(Money)
    transactibility_score: Mapped[Decimal | None] = mapped_column(Money)
    item_defect_count: Mapped[int | None]
    defect_ratio: Mapped[Decimal | None] = mapped_column(Money)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WalmartWfsInventory(Base):
    """Última foto de existencias WFS por (sku, nodo). Tabla ops.walmart_wfs_inventory."""

    __tablename__ = "walmart_wfs_inventory"
    __table_args__ = ({"schema": "ops"},)

    sku: Mapped[str] = mapped_column(String, primary_key=True)
    node: Mapped[str] = mapped_column(String, primary_key=True)
    offer_id: Mapped[str | None] = mapped_column(String)
    node_type: Mapped[str | None] = mapped_column(String)
    avail_to_sell: Mapped[int] = mapped_column(default=0)
    on_hand: Mapped[int] = mapped_column(default=0)
    first_in_stock_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WalmartWfsInventoryHistory(Base):
    """Historial diario acotado de existencias WFS. Tabla ops.walmart_wfs_inventory_history."""

    __tablename__ = "walmart_wfs_inventory_history"
    __table_args__ = ({"schema": "ops"},)

    sku: Mapped[str] = mapped_column(String, primary_key=True)
    node: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    avail_to_sell: Mapped[int] = mapped_column(default=0)
    on_hand: Mapped[int] = mapped_column(default=0)


class WalmartFeed(Base):
    """Carga (feed) enviada a Walmart y su resultado. Tabla ops.walmart_feed."""

    __tablename__ = "walmart_feed"
    __table_args__ = ({"schema": "ops"},)

    feed_id: Mapped[str] = mapped_column(String, primary_key=True)
    feed_type: Mapped[str | None] = mapped_column(String)
    feed_source: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    items_received: Mapped[int] = mapped_column(default=0)
    items_succeeded: Mapped[int] = mapped_column(default=0)
    items_failed: Mapped[int] = mapped_column(default=0)
    items_processing: Mapped[int] = mapped_column(default=0)
    data_error_count: Mapped[int] = mapped_column(default=0)
    system_error_count: Mapped[int] = mapped_column(default=0)
    feed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WalmartRepricerStrategy(Base):
    """Estrategia del repricer de Walmart con sus reglas en JSON. Tabla ops.walmart_repricer_strategy."""

    __tablename__ = "walmart_repricer_strategy"
    __table_args__ = ({"schema": "ops"},)

    collection_id: Mapped[str] = mapped_column(String, primary_key=True)
    strategy: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    assigned_count: Mapped[int] = mapped_column(default=0)
    enable_for_promotion: Mapped[bool | None] = mapped_column(Boolean)
    restore_seller_price_without_target: Mapped[bool | None] = mapped_column(Boolean)
    enable_buybox_meet_external: Mapped[bool | None] = mapped_column(Boolean)
    compare_with_3p_offer_only: Mapped[bool | None] = mapped_column(Boolean)
    strategies: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WalmartShippingTemplate(Base):
    """Plantilla de envío de Walmart con su detalle. Tabla ops.walmart_shipping_template."""

    __tablename__ = "walmart_shipping_template"
    __table_args__ = ({"schema": "ops"},)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    type: Mapped[str | None] = mapped_column(String)
    rate_model_type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    detail: Mapped[dict | None] = mapped_column(JSON)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class WalmartReportRequest(Base):
    """Solicitud de reporte de Walmart (ITEM, BUYBOX...). Tabla ops.walmart_report_request."""

    __tablename__ = "walmart_report_request"
    __table_args__ = ({"schema": "ops"},)

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    report_type: Mapped[str] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    report_version: Mapped[str | None] = mapped_column(String)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WalmartItemSnapshot(Base):
    """Foto por SKU tomada del último reporte ITEM. Tabla ops.walmart_item_snapshot."""

    __tablename__ = "walmart_item_snapshot"
    __table_args__ = ({"schema": "ops"},)

    sku: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str | None] = mapped_column(String)
    product_name: Mapped[str | None] = mapped_column(String)
    publish_status: Mapped[str | None] = mapped_column(String)
    lifecycle_status: Mapped[str | None] = mapped_column(String)
    status_change_reason: Mapped[str | None] = mapped_column(String)
    fulfillment_type: Mapped[str | None] = mapped_column(String)
    price: Mapped[Decimal | None] = mapped_column(Money)
    buybox_price: Mapped[Decimal | None] = mapped_column(Money)
    buybox_eligible: Mapped[bool | None] = mapped_column(Boolean)
    repricer_strategy: Mapped[str | None] = mapped_column(String)
    repricer_status: Mapped[str | None] = mapped_column(String)
    min_allowed_price: Mapped[Decimal | None] = mapped_column(Money)
    max_allowed_price: Mapped[Decimal | None] = mapped_column(Money)
    reviews_count: Mapped[int | None]
    average_rating: Mapped[Decimal | None] = mapped_column(Money)
    report_request_id: Mapped[str | None] = mapped_column(String)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokOrganicAccount(Base):
    """Foto diaria de la cuenta de TikTok orgánico (NO TikTok Shop). Tabla ops.tiktok_organic_account."""

    __tablename__ = "tiktok_organic_account"
    __table_args__ = ({"schema": "ops"},)

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_id: Mapped[str | None] = mapped_column(String)
    username: Mapped[str | None] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String)
    bio: Mapped[str | None] = mapped_column(String)
    profile_url: Mapped[str | None] = mapped_column(String)
    is_verified: Mapped[bool | None] = mapped_column(Boolean)
    follower_count: Mapped[int | None] = mapped_column(BigInteger)
    following_count: Mapped[int | None] = mapped_column(BigInteger)
    likes_count: Mapped[int | None] = mapped_column(BigInteger)
    video_count: Mapped[int | None]
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokOrganicVideo(Base):
    """Datos estables de un video orgánico. Tabla ops.tiktok_organic_video."""

    __tablename__ = "tiktok_organic_video"
    __table_args__ = ({"schema": "ops"},)

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    cover_url: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[int | None]
    share_url: Mapped[str | None] = mapped_column(String)
    embed_link: Mapped[str | None] = mapped_column(String)
    width: Mapped[int | None]
    height: Mapped[int | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokOrganicVideoMetric(Base):
    """Foto diaria de las métricas de un video (la API solo da el valor actual). ops.tiktok_organic_video_metric."""

    __tablename__ = "tiktok_organic_video_metric"
    __table_args__ = ({"schema": "ops"},)

    video_id: Mapped[str] = mapped_column(
        String, ForeignKey("ops.tiktok_organic_video.video_id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    views: Mapped[int] = mapped_column(BigInteger, default=0)
    likes: Mapped[int] = mapped_column(BigInteger, default=0)
    comments: Mapped[int] = mapped_column(BigInteger, default=0)
    shares: Mapped[int] = mapped_column(BigInteger, default=0)


# --- TikTok Shop: datos extra (catálogo, rendimiento, promociones, cupones, paquetes, pagos...) ---------------
# Tablas del esquema ops. Claves naturales = ids de TikTok (texto), así cada sincronización es idempotente.
# Solo lectura de la API: nada de esto se escribe de vuelta en TikTok.


class TikTokShopProduct(Base):
    """Producto del catálogo de TikTok Shop con su imagen principal (solo la URL). ops.tiktok_shop_product."""

    __tablename__ = "tiktok_shop_product"
    __table_args__ = ({"schema": "ops"},)

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String, index=True)
    audit_status: Mapped[str | None] = mapped_column(String)
    audit_reasons: Mapped[list | None] = mapped_column(JSON)
    listing_quality_tier: Mapped[str | None] = mapped_column(String)
    is_not_for_sale: Mapped[bool] = mapped_column(Boolean, default=False)
    main_image_url: Mapped[str | None] = mapped_column(String)
    thumb_url: Mapped[str | None] = mapped_column(String)
    image_count: Mapped[int] = mapped_column(default=0)
    brand_name: Mapped[str | None] = mapped_column(String)
    categories: Mapped[list | None] = mapped_column(JSON)
    sales_regions: Mapped[list | None] = mapped_column(JSON)
    min_price: Mapped[Decimal | None] = mapped_column(Money)
    max_price: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String)
    sku_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokShopProductSku(Base):
    """SKU de un producto: une el id de TikTok con el SKU del vendedor (seller_sku). ops.tiktok_shop_product_sku."""

    __tablename__ = "tiktok_shop_product_sku"
    __table_args__ = ({"schema": "ops"},)

    sku_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, index=True)
    seller_sku: Mapped[str | None] = mapped_column(String, index=True)
    price: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(default=0)


class TikTokShopProductPerformance(Base):
    """Foto diaria del rendimiento de un producto en una ventana. ops.tiktok_shop_product_performance."""

    __tablename__ = "tiktok_shop_product_performance"
    __table_args__ = ({"schema": "ops"},)

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    gmv: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    orders: Mapped[int] = mapped_column(default=0)
    units_sold: Mapped[int] = mapped_column(default=0)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    page_views: Mapped[int | None] = mapped_column(BigInteger)
    page_visitors: Mapped[int | None] = mapped_column(BigInteger)
    click_through_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    conversion_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    gmv_video: Mapped[Decimal | None] = mapped_column(Money)
    gmv_live: Mapped[Decimal | None] = mapped_column(Money)
    gmv_product_card: Mapped[Decimal | None] = mapped_column(Money)


class TikTokShopSkuPerformance(Base):
    """Foto diaria del rendimiento de un SKU en una ventana. ops.tiktok_shop_sku_performance."""

    __tablename__ = "tiktok_shop_sku_performance"
    __table_args__ = ({"schema": "ops"},)

    sku_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    product_id: Mapped[str | None] = mapped_column(String)
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    gmv: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    orders: Mapped[int] = mapped_column(default=0)
    units_sold: Mapped[int] = mapped_column(default=0)


class TikTokShopPromotion(Base):
    """Actividad de promoción (precio fijo, flash sale, envío...). ops.tiktok_shop_promotion."""

    __tablename__ = "tiktok_shop_promotion"
    __table_args__ = ({"schema": "ops"},)

    activity_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str | None] = mapped_column(String)
    activity_type: Mapped[str | None] = mapped_column(String)
    product_level: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String, index=True)
    duration_type: Mapped[str | None] = mapped_column(String)
    begin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    product_count: Mapped[int] = mapped_column(default=0)
    discount: Mapped[dict | None] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokShopPromotionProduct(Base):
    """Producto incluido en una actividad y su precio promocional. ops.tiktok_shop_promotion_product."""

    __tablename__ = "tiktok_shop_promotion_product"
    __table_args__ = ({"schema": "ops"},)

    activity_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    activity_price: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String)
    discount: Mapped[dict | None] = mapped_column(JSON)
    quantity_limit: Mapped[int | None]
    quantity_per_user: Mapped[int | None]


class TikTokShopCoupon(Base):
    """Cupón del vendedor. ops.tiktok_shop_coupon."""

    __tablename__ = "tiktok_shop_coupon"
    __table_args__ = ({"schema": "ops"},)

    coupon_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String, index=True)
    display_type: Mapped[str | None] = mapped_column(String)
    promo_code: Mapped[str | None] = mapped_column(String)
    product_scope: Mapped[str | None] = mapped_column(String)
    target_segment: Mapped[str | None] = mapped_column(String)
    discount_type: Mapped[str | None] = mapped_column(String)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    discount_amount: Mapped[Decimal | None] = mapped_column(Money)
    max_discount: Mapped[Decimal | None] = mapped_column(Money)
    threshold_type: Mapped[str | None] = mapped_column(String)
    min_spend: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String)
    claim_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeem_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redeem_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redemption_limit: Mapped[int | None]
    per_buyer_limit: Mapped[int | None]
    claimed_count: Mapped[int | None]
    redeemed_count: Mapped[int | None]
    product_ids: Mapped[list | None] = mapped_column(JSON)
    creation_source: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokShopPackage(Base):
    """Paquete de fulfillment: estados y tiempos, sin datos del comprador. ops.tiktok_shop_package."""

    __tablename__ = "tiktok_shop_package"
    __table_args__ = ({"schema": "ops"},)

    package_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str | None] = mapped_column(String, index=True)
    tracking_number: Mapped[str | None] = mapped_column(String)
    order_ids: Mapped[list | None] = mapped_column(JSON)
    order_count: Mapped[int] = mapped_column(default=0)
    unit_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event: Mapped[str | None] = mapped_column(String)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class TikTokShopPayment(Base):
    """Pago de TikTok al banco del vendedor (solo los últimos 4 dígitos de la cuenta). ops.tiktok_shop_payment."""

    __tablename__ = "tiktok_shop_payment"
    __table_args__ = ({"schema": "ops"},)

    payment_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str | None] = mapped_column(String, index=True)
    currency: Mapped[str | None] = mapped_column(String)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    settlement_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    reserve_amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    amount_before_exchange: Mapped[Decimal | None] = mapped_column(Money)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bank_last4: Mapped[str | None] = mapped_column(String)
    statement_ids: Mapped[list | None] = mapped_column(JSON)


class TikTokShopWithdrawal(Base):
    """Retiro de saldo. ops.tiktok_shop_withdrawal."""

    __tablename__ = "tiktok_shop_withdrawal"
    __table_args__ = ({"schema": "ops"},)

    withdrawal_id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String, index=True)
    currency: Mapped[str | None] = mapped_column(String)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class TikTokShopWarehouse(Base):
    """Almacén (sin líneas de calle ni contacto). ops.tiktok_shop_warehouse."""

    __tablename__ = "tiktok_shop_warehouse"
    __table_args__ = ({"schema": "ops"},)

    warehouse_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    type: Mapped[str | None] = mapped_column(String)
    sub_type: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[str | None] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)
    region_code: Mapped[str | None] = mapped_column(String)
    postal_code: Mapped[str | None] = mapped_column(String)


class TikTokShopWebhook(Base):
    """Suscripción a eventos (webhook) del vendedor. ops.tiktok_shop_webhook."""

    __tablename__ = "tiktok_shop_webhook"
    __table_args__ = ({"schema": "ops"},)

    event_type: Mapped[str] = mapped_column(String, primary_key=True)
    address: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
