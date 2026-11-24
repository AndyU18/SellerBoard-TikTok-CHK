"""Política de muestras: a quién seguir enviando, con qué tope y a quién cortar.

El problema: las muestras cuestan dinero real (producto + envío) y la mayoría de los creadores que las
reciben nunca venden. Este módulo toma el historial de quién vendió y produce tres cosas:

  1. Una PUNTUACIÓN 0-100 por creador, explicada por componentes con peso (conversión, utilidad, rapidez,
     constancia) para que nadie tenga que confiar en una caja negra.
  2. Una RECOMENDACIÓN (Seguir enviando / Enviar con tope / Pausar / Cortar) con el presupuesto de muestras
     sugerido por creador.
  3. Una SIMULACIÓN: se re-juega el historial aplicando la regla (tope de muestras sin venta y días máximos
     a la primera venta) para medir cuánto se habría ahorrado y cuántas ventas buenas habría cortado.

Reutiliza `creator_profit_report` (órdenes de afiliado + muestras + COGS por creador): aquí no se vuelve a
calcular ninguna utilidad. Solo se añaden las fechas que ese reporte no expone (cada muestra, primera venta,
semanas activas, tipo de contenido).

Límites conocidos de los datos (se repiten en `notes` de la respuesta):
  * El costo de cada muestra se aproxima con el promedio del creador (el reporte base solo da el total).
  * "Venta en riesgo" es una COTA SUPERIOR: no se puede saber si la venta vino de la muestra que la regla
    habría bloqueado o de una anterior.
  * Las muestras no se ligan a un producto/video: el tipo de contenido sale de las ventas del creador.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.creator_profit import creator_profit_report
from app.models import AffiliateOrder, SampleApplication, SampleLine
from app.reports import NO_CREATOR

log = logging.getLogger("chk.sample_policy")

# ---- recomendaciones
SEND, CAP, PAUSE, CUT = "Seguir enviando", "Enviar con tope", "Pausar", "Cortar"
RECOMMENDATIONS = (SEND, CAP, PAUSE, CUT)

# ---- pesos de la puntuación (suman 100: cada uno son los puntos máximos del componente)
WEIGHT_CONVERSION = 35.0
WEIGHT_PROFIT = 30.0
WEIGHT_SPEED = 20.0
WEIGHT_CONSISTENCY = 15.0
SCORE_WEIGHTS: dict[str, float] = {
    "conversion": WEIGHT_CONVERSION,
    "profit": WEIGHT_PROFIT,
    "speed": WEIGHT_SPEED,
    "consistency": WEIGHT_CONSISTENCY,
}
SCORE_LABELS = {
    "conversion": "Conversión",
    "profit": "Utilidad",
    "speed": "Rapidez",
    "consistency": "Constancia",
}

# ---- escalas de cada componente (con qué valor se obtiene el 100 % del componente)
FULL_ORDERS_PER_SAMPLE = 5.0  # 5 órdenes por muestra enviada = conversión plena
FULL_PAYBACK_MULTIPLE = 3.0  # recuperar 3x el costo de muestras (antes de muestras) = utilidad plena
FAST_SALE_DAYS = 7  # vender en una semana desde la muestra = rapidez plena
SLOW_SALE_DAYS = 60  # tardar 60 días o más = rapidez nula
FULL_ACTIVE_WEEKS = 4  # vender en 4 semanas distintas = constancia plena

# ---- cortes de la recomendación (sobre la puntuación)
SEND_SCORE = 60.0
CAP_SCORE = 30.0
MAX_SEND_SAMPLES = 5  # tope duro de muestras sugeridas aunque el creador sea excelente
SAMPLE_REINVEST_SHARE = 0.25  # de la utilidad que genera, se reinvierte hasta un 25 % en muestras
CAP_SUGGESTED_SAMPLES = 1  # "Enviar con tope": una muestra más y se vuelve a evaluar

# ---- simulación y proyección
MAX_CURVE_CAP = 10  # la curva de ahorro no pasa de este tope de muestras
MIN_PACE_SPAN_DAYS = 30  # evita proyectar el ritmo de envío sobre una ventana demasiado corta
PROJECTION_DAYS = 30
HISTOGRAM_BINS = 10
TOP_CREATOR_ACTIONS = 10

CONTENT_LABELS = {"VIDEO": "Video", "LIVE": "LIVE", "SHOP": "Tienda", "LINKSHARE": "Enlace"}

NOTES = (
    "El costo de cada muestra se aproxima con el promedio del creador (el reporte base solo da el total).",
    "Venta en riesgo es una cota superior: no se sabe si la venta vino de la muestra que la regla habría bloqueado.",
    "El tipo de contenido sale de las ventas del creador: las muestras no se ligan a un video ni a un LIVE.",
)


@dataclass(frozen=True, slots=True)
class CreatorHistory:
    """Lo que `creator_profit_report` no expone: cuándo ocurrió cada cosa."""

    sample_days: tuple[date, ...]  # un día por orden de muestra, ascendente
    first_sale: date | None
    active_weeks: int
    content_type: str | None


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """La regla que se simula: tope de muestras sin venta y plazo máximo a la primera venta."""

    max_samples_without_sale: int
    max_days_to_first_sale: int


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _today() -> date:
    """Hoy (UTC). Función aparte para poder fijarla en las pruebas."""
    return datetime.now(UTC).date()


def _week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


# --------------------------------------------------------------------------------------------- puntuación


def conversion_value(orders: int, samples: int) -> float:
    """Órdenes por muestra enviada, normalizado a 0-1. Sin muestras no hay conversión que medir."""
    if samples <= 0:
        return 0.0
    return _clamp01((orders / samples) / FULL_ORDERS_PER_SAMPLE)


def profit_value(profit_before_samples: float, sample_cost: float) -> float:
    """Cuántas veces recupera el creador lo que costaron sus muestras. Sin utilidad previa vale 0."""
    if profit_before_samples <= 0:
        return 0.0
    if sample_cost <= 0:
        return 1.0  # utilidad sin costo de muestra registrado: no se penaliza
    return _clamp01((profit_before_samples / sample_cost) / FULL_PAYBACK_MULTIPLE)


def speed_value(days_to_first_sale: int | None) -> float:
    """1 si vendió en `FAST_SALE_DAYS`, 0 si tardó `SLOW_SALE_DAYS` o más (lineal entre medio); 0 si no vendió."""
    if days_to_first_sale is None:
        return 0.0
    if days_to_first_sale <= FAST_SALE_DAYS:
        return 1.0
    return _clamp01((SLOW_SALE_DAYS - days_to_first_sale) / (SLOW_SALE_DAYS - FAST_SALE_DAYS))


def consistency_value(active_weeks: int) -> float:
    """Un solo pico de ventas no es un creador fiable: se premia vender en varias semanas distintas."""
    return _clamp01(active_weeks / FULL_ACTIVE_WEEKS)


def score_creator(
    *,
    orders: int,
    samples: int,
    profit_before_samples: float,
    sample_cost: float,
    days_to_first_sale: int | None,
    active_weeks: int,
) -> tuple[float, list[dict[str, Any]]]:
    """Puntuación 0-100 y su desglose (valor 0-1, peso y puntos de cada componente)."""
    values = {
        "conversion": conversion_value(orders, samples),
        "profit": profit_value(profit_before_samples, sample_cost),
        "speed": speed_value(days_to_first_sale),
        "consistency": consistency_value(active_weeks),
    }
    parts = [
        {
            "key": key,
            "label": SCORE_LABELS[key],
            "weight": SCORE_WEIGHTS[key],
            "value": round(value, 4),
            "points": round(value * SCORE_WEIGHTS[key], 2),
        }
        for key, value in values.items()
    ]
    return round(sum(p["points"] for p in parts), 2), parts


# ------------------------------------------------------------------------------------------ recomendación


def recommend(
    *,
    score: float,
    orders: int,
    samples: int,
    profit_before_samples: float,
    days_since_first_sample: int,
    rule: PolicyRule,
) -> tuple[str, str]:
    """Recomendación y su razón en una frase.

    Sin ventas manda la regla (plazo y tope); con ventas manda la puntuación, salvo que el creador venda
    sin dejar utilidad (más ventas no arreglan un margen negativo).
    """
    if orders == 0:
        if days_since_first_sample > rule.max_days_to_first_sale:
            return CUT, f"Sin ventas {days_since_first_sample} días después de la primera muestra"
        if samples >= rule.max_samples_without_sale:
            return PAUSE, f"{samples} muestras sin ninguna venta (tope {rule.max_samples_without_sale})"
        return CAP, "Aún dentro del plazo para vender: se espera el resultado de la muestra enviada"
    if profit_before_samples <= 0:
        return PAUSE, "Vende, pero sin utilidad antes de muestras"
    if score >= SEND_SCORE:
        return SEND, f"Puntuación {score:.0f}: convierte, deja utilidad y vende de forma constante"
    if score >= CAP_SCORE:
        return CAP, f"Puntuación {score:.0f}: rentable pero irregular; una muestra más y se reevalúa"
    return PAUSE, f"Puntuación {score:.0f}: ventas insuficientes para el costo de sus muestras"


def suggested_budget(
    recommendation: str, *, avg_sample_cost: float, profit_before_samples: float
) -> tuple[int, float]:
    """(muestras, USD) que se pueden seguir invirtiendo en el creador."""
    if recommendation in (PAUSE, CUT):
        return 0, 0.0
    if recommendation == CAP:
        return CAP_SUGGESTED_SAMPLES, round(CAP_SUGGESTED_SAMPLES * avg_sample_cost, 2)
    reinvest = SAMPLE_REINVEST_SHARE * max(profit_before_samples, 0.0)
    if avg_sample_cost <= 0:
        return MAX_SEND_SAMPLES, 0.0
    count = int(max(1, min(MAX_SEND_SAMPLES, reinvest // avg_sample_cost)))
    return count, round(count * avg_sample_cost, 2)


# ----------------------------------------------------------------------------------------------- simulación


def blocked_samples(history: CreatorHistory, rule: PolicyRule) -> list[date]:
    """Días de las muestras que la regla NO habría enviado, re-jugando el historial en orden.

    Una muestra se bloquea si, a su fecha, el creador aún no había vendido y además ya recibió el tope de
    muestras (todas las previas fueron sin venta) o ya pasó el plazo desde su primera muestra.
    """
    if not history.sample_days:
        return []
    first = history.sample_days[0]
    blocked: list[date] = []
    for index, day in enumerate(history.sample_days):
        already_sold = history.first_sale is not None and history.first_sale <= day
        if already_sold:
            continue
        over_cap = index >= rule.max_samples_without_sale
        over_time = (day - first).days > rule.max_days_to_first_sale
        if over_cap or over_time:
            blocked.append(day)
    return blocked


def simulate_rule(
    population: Sequence[dict[str, Any]], histories: dict[str, CreatorHistory], rule: PolicyRule
) -> dict[str, Any]:
    """Ahorro y falsos positivos de aplicar `rule` sobre todo el historial."""
    savings = lost_sales = lost_profit = 0.0
    saved_samples = 0
    stopped: set[str] = set()
    false_positives: set[str] = set()
    for row in population:
        history = histories[row["creator"]]
        blocked = blocked_samples(history, rule)
        if not blocked:
            continue
        stopped.add(row["creator"])
        saved_samples += len(blocked)
        savings += len(blocked) * row["avg_sample_cost"]
        # Falso positivo: vendió DESPUÉS de la primera muestra que la regla habría bloqueado.
        if history.first_sale is not None and history.first_sale > blocked[0]:
            false_positives.add(row["creator"])
            lost_sales += row["sales"]
            lost_profit += max(row["profit_before_samples"], 0.0)
    return {
        "max_samples_without_sale": rule.max_samples_without_sale,
        "max_days_to_first_sale": rule.max_days_to_first_sale,
        "creators_stopped": len(stopped),
        "samples_saved": saved_samples,
        "savings": round(savings, 2),
        "false_positives": len(false_positives),
        "lost_sales": round(lost_sales, 2),
        "lost_profit": round(lost_profit, 2),
        "net_benefit": round(savings - lost_profit, 2),
    }


def savings_curve(
    population: Sequence[dict[str, Any]], histories: dict[str, CreatorHistory], rule: PolicyRule
) -> list[dict[str, Any]]:
    """Ahorro vs tope de muestras (0..máximo observado), con los días máximos de la regla fija."""
    observed = max((len(h.sample_days) for h in histories.values()), default=0)
    top = min(max(observed, rule.max_samples_without_sale), MAX_CURVE_CAP)
    curve = []
    for cap in range(top + 1):
        point = simulate_rule(population, histories, PolicyRule(cap, rule.max_days_to_first_sale))
        curve.append({"cap": cap, **{k: point[k] for k in ("savings", "false_positives", "lost_profit", "net_benefit")}})
    return curve


def score_histogram(scores: Sequence[float]) -> list[dict[str, Any]]:
    """Cubetas de ancho 100/HISTOGRAM_BINS; el 100 exacto cae en la última."""
    width = 100 / HISTOGRAM_BINS
    counts = [0] * HISTOGRAM_BINS
    for s in scores:
        counts[min(int(s // width), HISTOGRAM_BINS - 1)] += 1
    return [{"from": round(i * width), "to": round((i + 1) * width), "count": c} for i, c in enumerate(counts)]


# ----------------------------------------------------------------------------------------------- datos


async def _load_histories(session: AsyncSession, start: date | None, end: date | None) -> dict[str, CreatorHistory]:
    sample_q = (
        select(SampleApplication.creator_username, SampleLine.external_order_id, func.min(SampleLine.order_date))
        .join(SampleApplication, SampleApplication.external_order_id == SampleLine.external_order_id)
        .where(SampleApplication.creator_username.is_not(None))
        .group_by(SampleApplication.creator_username, SampleLine.external_order_id)
    )
    if start:
        sample_q = sample_q.where(SampleLine.order_date >= start)
    if end:
        sample_q = sample_q.where(SampleLine.order_date <= end)
    sample_days: dict[str, list[date]] = {}
    for creator, _order, day in (await session.execute(sample_q)).all():
        if creator and creator != NO_CREATOR:
            sample_days.setdefault(creator, []).append(day)

    aff_q = select(
        AffiliateOrder.creator_username,
        AffiliateOrder.external_order_id,
        AffiliateOrder.order_date,
        AffiliateOrder.content_type,
    ).where(AffiliateOrder.creator_username.is_not(None))
    if start:
        aff_q = aff_q.where(AffiliateOrder.order_date >= start)
    if end:
        aff_q = aff_q.where(AffiliateOrder.order_date <= end)
    first_sale: dict[str, date] = {}
    weeks: dict[str, set[date]] = {}
    kinds: dict[str, Counter[str]] = {}
    seen: set[tuple[str, str, str]] = set()  # una orden cuenta una vez por tipo de contenido
    for creator, order_id, day, kind in (await session.execute(aff_q)).all():
        if creator not in sample_days:
            continue
        first_sale[creator] = min(first_sale.get(creator, day), day)
        weeks.setdefault(creator, set()).add(_week_of(day))
        label = (kind or "").upper()
        if label and (creator, order_id, label) not in seen:
            seen.add((creator, order_id, label))
            kinds.setdefault(creator, Counter())[label] += 1

    histories: dict[str, CreatorHistory] = {}
    for creator, days in sample_days.items():
        counter = kinds.get(creator)
        dominant = min(counter, key=lambda k: (-counter[k], k)) if counter else None
        histories[creator] = CreatorHistory(
            sample_days=tuple(sorted(days)),
            first_sale=first_sale.get(creator),
            active_weeks=len(weeks.get(creator, ())),
            content_type=CONTENT_LABELS.get(dominant, dominant.title()) if dominant else None,
        )
    return histories


def _creator_row(
    creator: str, history: CreatorHistory, money: dict[str, Any], rule: PolicyRule, today: date
) -> dict[str, Any]:
    samples = len(history.sample_days)
    sample_cost = _f(money.get("sample_cost"))
    before = _f(money.get("profit_before_samples"))
    orders = int(money.get("orders") or 0)
    first_sample = history.sample_days[0]
    days_to_sale = max(0, (history.first_sale - first_sample).days) if history.first_sale else None
    days_since = max(0, (today - first_sample).days)
    avg_cost = sample_cost / samples if samples else 0.0

    score, parts = score_creator(
        orders=orders,
        samples=samples,
        profit_before_samples=before,
        sample_cost=sample_cost,
        days_to_first_sale=days_to_sale,
        active_weeks=history.active_weeks,
    )
    recommendation, reason = recommend(
        score=score,
        orders=orders,
        samples=samples,
        profit_before_samples=before,
        days_since_first_sample=days_since,
        rule=rule,
    )
    budget_samples, budget_usd = suggested_budget(
        recommendation, avg_sample_cost=avg_cost, profit_before_samples=before
    )
    return {
        "creator": creator,
        "samples": samples,
        "sample_cost": round(sample_cost, 2),
        "avg_sample_cost": round(avg_cost, 4),
        "orders": orders,
        "sales": round(_f(money.get("sales")), 2),
        "profit_before_samples": round(before, 2),
        "profit": round(_f(money.get("profit")), 2),
        "first_sample": first_sample.isoformat(),
        "first_sale": history.first_sale.isoformat() if history.first_sale else None,
        "days_to_first_sale": days_to_sale,
        "days_since_first_sample": days_since,
        "content_type": history.content_type,
        "active_weeks": history.active_weeks,
        "sample_conversion": round(orders / samples, 4) if samples else None,
        "score": score,
        "score_parts": parts,
        "recommendation": recommendation,
        "reason": reason,
        "suggested_samples": budget_samples,
        "suggested_budget_usd": budget_usd,
    }


async def sample_policy_report(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    max_samples_without_sale: int = 2,
    max_days_to_first_sale: int = 30,
    today: date | None = None,
) -> dict[str, Any]:
    if max_samples_without_sale < 0:
        raise ValueError("max_samples_without_sale no puede ser negativo")
    if max_days_to_first_sale < 0:
        raise ValueError("max_days_to_first_sale no puede ser negativo")
    today = today or _today()
    rule =PolicyRule(max_samples_without_sale, max_days_to_first_sale)

    histories = await _load_histories(session, start, end)
    base = await creator_profit_report(session, start=start, end=end, level="creator", limit=10**9, today=today)
    money = {r["key"]: r for r in base["rows"]}

    rows = [_creator_row(c, h, money.get(c, {}), rule, today) for c, h in histories.items()]
    rows.sort(key=lambda r: (-r["score"], r["creator"]))
    simulation = simulate_rule(rows, histories, rule)
    counts = Counter(r["recommendation"] for r in rows)
    base_summary = base["summary"]
    profit_after_samples = _f(base_summary["affiliate_profit"])
    summary = {
        "creators": len(rows),
        "recommendation_counts": {name: counts.get(name, 0) for name in RECOMMENDATIONS},
        "to_pause": counts.get(PAUSE, 0) + counts.get(CUT, 0),
        "sample_cost": round(sum(r["sample_cost"] for r in rows), 2),
        "never_sold_creators": base_summary["never_sold_creators"],
        "never_sold_cost": round(_f(base_summary["never_sold_cost"]), 2),
        "estimated_savings": simulation["savings"],
        "false_positives": simulation["false_positives"],
        "lost_sales": simulation["lost_sales"],
        "lost_profit": simulation["lost_profit"],
        "net_benefit": simulation["net_benefit"],
        "profit_before_samples": round(_f(base_summary["affiliate_profit_before_samples"]), 2),
        "profit_after_samples": round(profit_after_samples, 2),
        "profit_with_policy": round(profit_after_samples + simulation["savings"] - simulation["lost_profit"], 2),
    }
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "today": today.isoformat(),
        "params": {
            "max_samples_without_sale": max_samples_without_sale,
            "max_days_to_first_sale": max_days_to_first_sale,
        },
        "weights": SCORE_WEIGHTS,
        "thresholds": {"send_score": SEND_SCORE, "cap_score": CAP_SCORE},
        "summary": summary,
        "simulation": simulation,
        "curve": savings_curve(rows, histories, rule),
        "histogram": score_histogram([r["score"] for r in rows]),
        "rows": rows,
        "notes": list(NOTES),
    }


# ------------------------------------------------------------------------------------ Centro de acciones


def _unreturned_cost(row: dict[str, Any]) -> float:
    """Costo de muestras que la utilidad del creador no recuperó (nunca negativo)."""
    return max(0.0, row["sample_cost"] - max(row["profit_before_samples"], 0.0))


def _pace_factor(rows: Sequence[dict[str, Any]], today: date) -> float:
    """Convierte lo gastado en el historial a lo que se gastaría en `PROJECTION_DAYS`.

    El ritmo histórico es costo / días observados, donde la ventana va de la primera muestra de la cuenta a hoy
    (mínimo `MIN_PACE_SPAN_DAYS`, para no inflar la proyección con poca historia).
    """
    earliest = min(date.fromisoformat(r["first_sample"]) for r in rows)
    span = max(MIN_PACE_SPAN_DAYS, (today - earliest).days)
    return PROJECTION_DAYS / span


@action_provider("sample_policy", "Política de muestras")
async def sample_policy_actions(session: AsyncSession) -> list[ActionItem]:
    """Una acción agrupada para los creadores a Pausar/Cortar y una por cada uno de los de mayor costo sin retorno."""
    today = _today()
    rows = (await sample_policy_report(session, today=today))["rows"]
    flagged = [r for r in rows if r["recommendation"] in (PAUSE, CUT) and _unreturned_cost(r) > 0]
    if not flagged:
        return []
    pace = _pace_factor(rows, today)
    flagged.sort(key=lambda r: (-_unreturned_cost(r), r["creator"]))
    top, rest = flagged[:TOP_CREATOR_ACTIONS], flagged[TOP_CREATOR_ACTIONS:]
    basis_tail = (
        f"costo de muestras sin retorno de estos creadores × ritmo histórico de envío proyectado a {PROJECTION_DAYS} días"
    )

    items: list[ActionItem] = []
    for row in top:
        items.append(
            ActionItem(
                key=f"sample_policy:creator:{row['creator']}",
                kind="sample_policy",
                title=f"{row['recommendation']} muestras a {row['creator']}",
                detail=(
                    f"{row['samples']} muestra(s), ${row['sample_cost']:,.0f} de costo y "
                    f"${row['profit_before_samples']:,.0f} de utilidad antes de muestras. {row['reason']}."
                ),
                impact_usd=round(_unreturned_cost(row) * pace, 2),
                impact_basis=f"Su costo sin retorno × ritmo histórico de envío a {PROJECTION_DAYS} días",
                effort="low",
                target="creatorProfit",
                entity=row["creator"],
                meta={"score": row["score"], "recommendation": row["recommendation"]},
            )
        )
    if rest:
        total = sum(_unreturned_cost(r) for r in rest)
        items.append(
            ActionItem(
                key="sample_policy:pause_cut",
                kind="sample_policy",
                title=f"Pausar/Cortar muestras a {len(flagged)} creadores",
                detail=(
                    f"{len(flagged)} creadores no recuperan sus muestras; {len(rest)} no tienen acción propia aquí "
                    f"y suman ${total:,.0f} de costo sin retorno."
                ),
                impact_usd=round(total * pace, 2),
                impact_basis=(
                    f"{basis_tail}; excluye los {len(top)} creadores con acción propia para no contar dos veces"
                ),
                effort="low",
                target="creatorProfit",
                meta={"creators": len(flagged), "creators_in_group": len(rest)},
            )
        )
    return items
