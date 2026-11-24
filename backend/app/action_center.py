"""Centro de acciones: lo que conviene hacer ahora, ordenado por dinero en juego.

Arquitectura (Registry + Strategy):
  * Cada área del negocio aporta un PROVEEDOR: una función async `(session) -> list[ActionItem]` que mira
    sus propios datos y devuelve acciones concretas. Se registra con `@action_provider(kind, label)`.
  * Este módulo no conoce a ningún proveedor (los importa `app.action_providers`): agregar una área nueva
    es escribir un proveedor, sin tocar el núcleo.
  * Las acciones se CALCULAN en cada consulta; lo único que se guarda es el estado que les da el usuario
    (ops.action_status: hecha, descartada, pospuesta). La clave de la acción es estable
    ("<kind>:<entidad>"), así el estado sobrevive entre cálculos.
  * Un proveedor que falla no tumba al resto: su error queda en `errors` de la respuesta.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActionStatus

log = logging.getLogger("chk.actions")

Effort = Literal["low", "medium", "high"]
Status = Literal["open", "done", "dismissed", "snoozed"]
SETTABLE = {"open", "done", "dismissed", "snoozed"}
MAX_NOTE = 500
MAX_SNOOZE_DAYS = 365
DEFAULT_SNOOZE_DAYS = 7
DONE_WINDOW_DAYS = 7  # "resuelto esta semana"


@dataclass(frozen=True, slots=True)
class ActionItem:
    """Una acción concreta. `impact_usd` es una ESTIMACIÓN; `impact_basis` explica cómo se calculó."""

    key: str  # estable y único: "<kind>:<entidad>"
    kind: str
    title: str
    detail: str
    impact_usd: float
    impact_basis: str
    effort: Effort
    target: str  # id de la pantalla de la app donde se resuelve (p. ej. "redoCorrections")
    entity: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


Provider = Callable[[AsyncSession], Awaitable[list[ActionItem]]]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    kind: str
    label: str
    fn: Provider


_REGISTRY: dict[str, ProviderSpec] = {}


def action_provider(kind: str, label: str) -> Callable[[Provider], Provider]:
    """Decorador que registra un proveedor. `kind` es único y prefija la clave de sus acciones."""

    def register(fn: Provider) -> Provider:
        if kind in _REGISTRY and _REGISTRY[kind].fn is not fn:
            raise ValueError(f"Ya hay un proveedor de acciones '{kind}'")
        _REGISTRY[kind] = ProviderSpec(kind, label, fn)
        return fn

    return register


def providers() -> dict[str, ProviderSpec]:
    return dict(_REGISTRY)


def _valid(item: ActionItem) -> bool:
    return item.key.startswith(f"{item.kind}:") and item.impact_usd == item.impact_usd  # descarta NaN


async def collect(session: AsyncSession, only: set[str] | None = None) -> tuple[list[ActionItem], list[dict[str, str]]]:
    """Ejecuta los proveedores (uno tras otro: la sesión no admite concurrencia) y junta sus acciones."""
    items: dict[str, ActionItem] = {}
    errors: list[dict[str, str]] = []
    for kind, spec in _REGISTRY.items():
        if only and kind not in only:
            continue
        try:
            produced = await spec.fn(session)
        except Exception as exc:  # noqa: BLE001 - un proveedor roto no debe tumbar la pantalla
            log.warning("Proveedor de acciones %s falló: %s", kind, exc)
            await session.rollback()
            errors.append({"kind": kind, "label": spec.label, "error": str(exc)[:200]})
            continue
        for item in produced:
            if not _valid(item):
                log.warning("Acción descartada (clave o impacto inválidos): %s", item.key)
                continue
            items.setdefault(item.key, item)
    return list(items.values()), errors


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def effective_status(row: ActionStatus | None, today: date) -> Status:
    """Estado vigente: una pospuesta cuyo plazo ya venció vuelve a estar abierta."""
    if row is None:
        return "open"
    if row.status == "snoozed" and row.snoozed_until is not None and row.snoozed_until <= today:
        return "open"
    return row.status  # type: ignore[return-value]


async def list_actions(
    session: AsyncSession,
    *,
    status: str = "open",
    kind: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    items, errors = await collect(session, {kind} if kind else None)
    rows = {r.item_key: r for r in (await session.scalars(select(ActionStatus))).all()}

    def view(item: ActionItem) -> dict[str, Any]:
        row = rows.get(item.key)
        current = effective_status(row, today)
        return {
            "key": item.key,
            "kind": item.kind,
            "kind_label": _REGISTRY[item.kind].label if item.kind in _REGISTRY else item.kind,
            "title": item.title,
            "detail": item.detail,
            "impact_usd": round(item.impact_usd, 2),
            "impact_basis": item.impact_basis,
            "effort": item.effort,
            "target": item.target,
            "entity": item.entity,
            "meta": item.meta,
            "status": current,
            "note": row.note if row else None,
            "snoozed_until": row.snoozed_until if row and current == "snoozed" else None,
            "updated_at": _aware(row.updated_at) if row else None,
            "updated_by": row.updated_by_email if row else None,
        }

    views = [view(i) for i in items]
    wanted = views if status == "all" else [v for v in views if v["status"] == status]
    wanted.sort(key=lambda v: (-v["impact_usd"], v["key"]))

    open_items = [v for v in views if v["status"] == "open"]
    by_kind: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "impact_usd": 0.0})
    for v in open_items:
        by_kind[v["kind"]]["count"] += 1
        by_kind[v["kind"]]["impact_usd"] += v["impact_usd"]
    since = datetime.now(UTC) - timedelta(days=DONE_WINDOW_DAYS)
    done_recent = [v for v in views if v["status"] == "done" and v["updated_at"] and v["updated_at"] >= since]
    return {
        "summary": {
            "open": len(open_items),
            "open_impact_usd": round(sum(v["impact_usd"] for v in open_items), 2),
            "done_this_week": len(done_recent),
            "done_this_week_impact_usd": round(sum(v["impact_usd"] for v in done_recent), 2),
            "snoozed": sum(1 for v in views if v["status"] == "snoozed"),
            "dismissed": sum(1 for v in views if v["status"] == "dismissed"),
        },
        "by_kind": [
            {
                "kind": k,
                "label": _REGISTRY[k].label if k in _REGISTRY else k,
                "count": d["count"],
                "impact_usd": round(d["impact_usd"], 2),
            }
            for k, d in sorted(by_kind.items(), key=lambda kv: -kv[1]["impact_usd"])
        ],
        "kinds": [{"kind": k, "label": s.label} for k, s in _REGISTRY.items()],
        "errors": errors,
        "items": wanted,
    }


class ActionError(ValueError):
    """Petición inválida sobre una acción (la API la convierte en 422)."""


async def set_status(
    session: AsyncSession,
    key: str,
    status: str,
    *,
    note: str | None = None,
    snooze_days: int | None = None,
    user_id: Any = None,
    user_email: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Marca una acción como hecha, descartada o pospuesta; "open" la reabre (borra su estado)."""
    if status not in SETTABLE:
        raise ActionError(f"Estado inválido: {status}")
    if ":" not in key or len(key) > 200:
        raise ActionError("Clave de acción inválida")
    note = (note or "").strip()[:MAX_NOTE] or None
    today = today or datetime.now(UTC).date()

    if status == "open":
        await session.execute(delete(ActionStatus).where(ActionStatus.item_key == key))
        await session.commit()
        return {"key": key, "status": "open"}

    until: date | None = None
    if status == "snoozed":
        days = snooze_days if snooze_days is not None else DEFAULT_SNOOZE_DAYS
        if not 1 <= days <= MAX_SNOOZE_DAYS:
            raise ActionError(f"Pospón entre 1 y {MAX_SNOOZE_DAYS} días")
        until = today + timedelta(days=days)

    row = await session.get(ActionStatus, key)
    if row is None:
        row = ActionStatus(item_key=key, status=status)
        session.add(row)
    row.status, row.note, row.snoozed_until = status, note, until
    row.updated_by, row.updated_by_email = user_id, user_email
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return {"key": key, "status": status, "snoozed_until": until}
