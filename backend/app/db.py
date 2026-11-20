from collections.abc import AsyncIterator

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import Base, Channel, ChannelRow, CostComponentType

SCHEMAS = ("sales", "catalog", "finance", "ops", "gold", "app", "bronze")


def make_engine(url: str) -> AsyncEngine:
    """Crea el motor. SQLite (desarrollo/tests) no tiene esquemas: se quitan con translate map."""
    if url.startswith("sqlite"):
        return create_async_engine(url).execution_options(
            schema_translate_map=dict.fromkeys(SCHEMAS)
        )
    # El pooler de Supabase (modo transacción) no admite prepared statements de asyncpg.
    return create_async_engine(url, connect_args={"statement_cache_size": 0}, pool_pre_ping=True)


engine = make_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def create_dev_schema(conn: Connection) -> None:
    """SQLite: crea las tablas y las filas fijas que en Supabase siembran las migraciones."""
    Base.metadata.create_all(conn)
    if not conn.scalar(select(ChannelRow.code).limit(1)):
        conn.execute(insert(ChannelRow), [{"code": c.value, "name": c.value} for c in Channel])
    if not conn.scalar(select(CostComponentType.id).limit(1)):
        conn.execute(insert(CostComponentType).values(code="product_cost", name="Costo del producto"))


async def init_db() -> None:
    """Desarrollo (SQLite): crea el esquema. En Postgres/Supabase lo gestionan las migraciones."""
    if engine.dialect.name == "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(create_dev_schema)
