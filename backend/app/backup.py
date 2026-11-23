"""Copias de seguridad lógicas: todas las tablas de la aplicación a un .zip de CSV (+ manifiesto).

Uso (desde backend/):
    uv run python -m app.backup create [--out backups] [--keep 14]
    uv run python -m app.backup list   [--out backups]
    uv run python -m app.backup restore ARCHIVO.zip [--force]

Qué es y qué no es:
  * **Crear** funciona con cualquier base (SQLite o Postgres/Supabase) y solo LEE: es seguro correrlo en
    producción. Guarda las tablas que modela la aplicación (ventas, catálogo, costos, gold, etc.); no
    incluye usuarios de Supabase Auth ni roles. Es una copia portátil, no sustituye los respaldos del
    proveedor (Supabase) ni un `pg_dump` completo.
  * **Restaurar** solo está soportado en SQLite (desarrollo). En Postgres usa `pg_restore`/`psql` con un
    volcado de `pg_dump` o la restauración de Supabase: ahí hay secuencias, identidades y permisos que
    una copia lógica no debe tocar a ciegas.
  * Sin `--force`, restaurar exige que las tablas de datos estén vacías. Con `--force` reemplaza todo.

Formato: una entrada `<esquema>.<tabla>.csv` por tabla, NULL = `\\N`, fechas ISO, JSON como texto.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    Table,
    Uuid,
    delete,
    func,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import Base

FORMAT_VERSION = 1
NULL = "\\N"
PREFIX = "chk-backup-"
# Tablas de referencia que ya existen en una base nueva (las siembran las migraciones o create_dev_schema).
REFERENCE_TABLES = {"sales.channel", "catalog.cost_component_type", "finance.expense_category"}


class BackupError(RuntimeError):
    """No se pudo crear o restaurar la copia."""


def table_key(table: Table) -> str:
    return f"{table.schema}.{table.name}" if table.schema else table.name


def _columns(table: Table) -> list[Any]:
    """Columnas con valor propio: las generadas por la base (net_profit) se recalculan solas."""
    return [c for c in table.columns if c.computed is None]


def to_cell(value: Any) -> str:
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = str(value)
    # Un texto que empieza con "\" duplica esa barra: así nunca se confunde con el marcador NULL ("\N").
    return "\\" + text if text.startswith("\\") else text


def from_cell(text: str, column: Any) -> Any:
    if text == NULL:
        return None
    if text.startswith("\\\\"):
        text = text[1:]
    kind = column.type
    if isinstance(kind, Boolean):
        return text == "true"
    if isinstance(kind, Integer):
        return int(text)
    if isinstance(kind, Numeric):
        return Decimal(text)
    if isinstance(kind, DateTime):
        return datetime.fromisoformat(text)
    if isinstance(kind, Date):
        return date.fromisoformat(text)
    if isinstance(kind, JSON):
        return json.loads(text)
    if isinstance(kind, Uuid):
        return UUID(text)
    return text


async def create_backup(
    engine: AsyncEngine, out_dir: Path, keep: int | None = None, now: datetime | None = None
) -> Path:
    """Escribe `chk-backup-<fecha>.zip` en `out_dir` y borra las más viejas si se pide `keep`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    final = out_dir / f"{PREFIX}{stamp}.zip"
    partial = final.with_suffix(".zip.part")
    counts: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                for table in Base.metadata.sorted_tables:
                    columns = _columns(table)
                    buffer = io.StringIO(newline="")
                    writer = csv.writer(buffer, lineterminator="\n")
                    writer.writerow([c.name for c in columns])
                    rows = (await conn.execute(select(*columns))).all()
                    for row in rows:
                        writer.writerow([to_cell(v) for v in row])
                    counts[table_key(table)] = len(rows)
                    archive.writestr(f"{table_key(table)}.csv", buffer.getvalue())
                manifest = {
                    "format": FORMAT_VERSION,
                    "created_at": (now or datetime.now(UTC)).isoformat(),
                    "dialect": engine.dialect.name,
                    "tables": counts,
                }
                archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        partial.replace(final)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if keep is not None and keep > 0:
        for old in list_backups(out_dir)[keep:]:
            old.unlink()
    return final


def list_backups(out_dir: Path) -> list[Path]:
    """Copias de `out_dir`, de la más nueva a la más vieja."""
    return sorted(out_dir.glob(f"{PREFIX}*.zip"), reverse=True) if out_dir.exists() else []


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise BackupError(f"No es una copia válida: {exc}") from exc
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupError(f"Formato de copia no soportado: {manifest.get('format')}")
    return manifest


async def restore_backup(engine: AsyncEngine, path: Path, force: bool = False) -> dict[str, int]:
    """Carga una copia en una base SQLite. Devuelve las filas insertadas por tabla."""
    if engine.dialect.name != "sqlite":
        raise BackupError(
            "La restauración automática solo está disponible en SQLite. En Postgres/Supabase usa pg_restore con "
            "un volcado de pg_dump o la restauración del proveedor."
        )
    manifest = read_manifest(path)
    tables = {table_key(t): t for t in Base.metadata.sorted_tables}
    unknown = sorted(set(manifest["tables"]) - set(tables))
    if unknown:
        raise BackupError(f"La copia trae tablas que esta versión no conoce: {unknown}")

    inserted: dict[str, int] = {}
    async with engine.begin() as conn:
        ordered = [t for t in Base.metadata.sorted_tables if table_key(t) in manifest["tables"]]
        if force:
            for table in reversed(ordered):
                await conn.execute(delete(table))
        else:
            for table in ordered:
                key = table_key(table)
                if key in REFERENCE_TABLES:
                    continue
                if (await conn.scalar(select(func.count()).select_from(table))):
                    raise BackupError(f"La tabla {key} ya tiene datos; usa --force para reemplazar todo.")
        with zipfile.ZipFile(path) as archive:
            for table in ordered:
                key = table_key(table)
                if key in REFERENCE_TABLES and not force and await conn.scalar(select(func.count()).select_from(table)):
                    inserted[key] = 0
                    continue
                columns = {c.name: c for c in _columns(table)}
                reader = csv.reader(io.StringIO(archive.read(f"{key}.csv").decode("utf-8"), newline=""))
                header = next(reader, [])
                missing = [name for name in header if name not in columns]
                if missing:
                    raise BackupError(f"{key}: columnas desconocidas {missing}")
                rows = [
                    {name: from_cell(cell, columns[name]) for name, cell in zip(header, record, strict=True)}
                    for record in reader
                ]
                for start in range(0, len(rows), 500):
                    await conn.execute(insert(table), rows[start : start + 500])
                inserted[key] = len(rows)
    return inserted


async def _run(args: argparse.Namespace) -> int:
    from app.db import engine

    out = Path(args.out)
    try:
        if args.command == "create":
            path = await create_backup(engine, out, keep=args.keep)
            manifest = read_manifest(path)
            total = sum(manifest["tables"].values())
            print(f"Copia creada: {path} ({total} filas en {len(manifest['tables'])} tablas)")
        elif args.command == "list":
            for path in list_backups(out):
                print(f"{path.name}  {path.stat().st_size / 1024:.0f} KB")
        else:
            result = await restore_backup(engine, Path(args.file), force=args.force)
            print(f"Restauradas {sum(result.values())} filas en {len(result)} tablas")
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.backup", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="crea una copia")
    create.add_argument("--out", default="backups", help="carpeta de destino (por defecto backups/)")
    create.add_argument("--keep", type=int, default=None, help="conservar solo las N copias más nuevas")
    listing = sub.add_parser("list", help="lista las copias")
    listing.add_argument("--out", default="backups")
    restore = sub.add_parser("restore", help="restaura una copia (solo SQLite)")
    restore.add_argument("file")
    restore.add_argument("--force", action="store_true", help="reemplaza los datos existentes")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
