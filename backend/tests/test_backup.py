"""Copias de seguridad: ida y vuelta, rotación, protecciones y casos raros de texto."""

import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import backup
from app.db import create_dev_schema, make_engine
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders
from app.models import Expense, ExpenseCategory, Goal, Product, SyncRun

TODAY = datetime.now(UTC).date()


async def fresh_engine():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    return engine


async def seed(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    df = pl.DataFrame(
        [
            {
                "date": TODAY - timedelta(days=i),
                "channel": "walmart",
                "account": "walmart_1",
                "order_id": f"O{i}",
                "sku": "A",
                "product_name": "Producto \\Nuevo",  # empieza como el marcador NULL a propósito en el caso de abajo
                "units": 2,
                "gross_sales": 40.5,
                "promo": 0.0,
                "platform_fees": 6.0,
                "shipping_cost": 3.25,
                "refund_amount": 0.0,
            }
            for i in range(3)
        ]
    )
    async with maker() as s:
        await import_orders(s, df)
        await refresh_daily_profit(s, None, None, reason="test")
        category = await s.scalar(select(ExpenseCategory.id).limit(1))
        s.add(Expense(category_id=category, description="\\N", amount=Decimal("12.50"), recurrence="monthly", start_date=date(2026, 1, 1)))
        s.add(Goal(month=date(2026, 3, 1), metric="net_profit", target=Decimal("5000.00")))
        s.add(SyncRun(job_type="walmart", status="success", stats={"lines": 3, "nested": {"ok": True}}))
        await s.commit()


async def counts(engine) -> dict[str, int]:
    out = {}
    async with engine.connect() as conn:
        for table in backup.Base.metadata.sorted_tables:
            out[backup.table_key(table)] = await conn.scalar(select(func.count()).select_from(table))
    return out


def test_cells_round_trip_awkward_values():
    from app.models import Expense as E

    description = E.__table__.c.description
    for value in ["", "abc", "\\N", "\\\\N", "\\algo", "línea\ncon salto", "coma, y \"comillas\""]:
        assert backup.from_cell(backup.to_cell(value), description) == value
    assert backup.from_cell(backup.NULL, description) is None
    assert backup.to_cell(None) == backup.NULL and backup.to_cell("") == ""


@pytest.mark.asyncio
async def test_backup_and_restore_round_trip(tmp_path):
    source = await fresh_engine()
    await seed(source)
    path = await backup.create_backup(source, tmp_path, now=datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    assert path.name == "chk-backup-20261123T120000Z.zip"
    manifest = backup.read_manifest(path)
    assert manifest["tables"]["sales.order_line"] == 3 and manifest["dialect"] == "sqlite"
    with zipfile.ZipFile(path) as z:
        assert "gold.daily_profit.csv" in z.namelist()
        header = z.read("gold.daily_profit.csv").decode().splitlines()[0]
        assert "net_profit" not in header  # columna generada: la base la recalcula

    target = await fresh_engine()
    restored = await backup.restore_backup(target, path)
    assert restored["sales.order_line"] == 3
    assert await counts(target) == await counts(source)

    maker = async_sessionmaker(target, expire_on_commit=False)
    async with maker() as s:
        assert (await s.scalar(select(Expense.description))) == "\\N"
        assert (await s.scalar(select(Goal.target))) == Decimal("5000.00")
        assert (await s.scalar(select(SyncRun.stats)))["nested"] == {"ok": True}
        assert (await s.scalar(select(Product.name))) == "Producto \\Nuevo"
        # la utilidad generada se recalculó sola al restaurar
        assert (await s.execute(text("select round(sum(net_profit), 2) from daily_profit"))).scalar() is not None
    await source.dispose()
    await target.dispose()


@pytest.mark.asyncio
async def test_restore_refuses_non_empty_database_without_force(tmp_path):
    source = await fresh_engine()
    await seed(source)
    path = await backup.create_backup(source, tmp_path)
    with pytest.raises(backup.BackupError, match="ya tiene datos"):
        await backup.restore_backup(source, path)  # la misma base: ya está llena
    replaced = await backup.restore_backup(source, path, force=True)
    assert replaced["sales.order_line"] == 3
    assert (await counts(source))["sales.order_line"] == 3  # reemplazó, no duplicó
    await source.dispose()


@pytest.mark.asyncio
async def test_rotation_keeps_only_the_newest(tmp_path):
    engine = await fresh_engine()
    for day in range(1, 5):
        await backup.create_backup(engine, tmp_path, keep=2, now=datetime(2026, 11, day, tzinfo=UTC))
    names = [p.name for p in backup.list_backups(tmp_path)]
    assert names == ["chk-backup-20261104T000000Z.zip", "chk-backup-20261103T000000Z.zip"]
    assert not list(tmp_path.glob("*.part"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_files_and_postgres_guard(tmp_path):
    engine = await fresh_engine()
    junk = tmp_path / "x.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(backup.BackupError):
        await backup.restore_backup(engine, junk)

    future = tmp_path / "future.zip"
    with zipfile.ZipFile(future, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 99, "tables": {}}))
    with pytest.raises(backup.BackupError, match="no soportado"):
        backup.read_manifest(future)

    class FakePostgres:
        class dialect:
            name = "postgresql"

    with pytest.raises(backup.BackupError, match="solo está disponible en SQLite"):
        await backup.restore_backup(FakePostgres(), future)  # type: ignore[arg-type]
    await engine.dispose()
