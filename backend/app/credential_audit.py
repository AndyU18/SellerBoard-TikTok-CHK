"""Auditoría de permisos de las plataformas: qué puede hacer cada token, qué sobra y qué conviene quitar.

Principio: esta aplicación SOLO LEE. Todo permiso de escritura o eliminación que tenga un token es riesgo sin
beneficio: si el token se filtra, quien lo tenga puede cambiar o borrar datos reales de la tienda. Por eso cada
permiso se clasifica (lectura / escritura / eliminación), se describe el daño posible y se marca si la app lo
usa hoy (verificado contra las rutas que llaman los conectores: todas son consultas).

Clasificación en dos capas:
  1. `KNOWN_SCOPES`: permisos conocidos, con texto en español y si la app los usa.
  2. Regla de respaldo por patrón (`.write`, `.delete`, `publish`, `edit`...) para permisos nuevos: se marcan
     "sin documentar: revisar" aunque la regla les dé un nivel, porque nadie ha confirmado qué permiten.

Esta capa es pura (sin red, sin archivos, sin base de datos): la fuente de los permisos concedidos llega por
parámetro (foto de ops.credential_scope_snapshot o las constantes verificadas de cada plataforma).
Nada de lo que se maneje aquí es un valor secreto: solo nombres de permisos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from app.credential_catalog import (
    EXPOSED_IN_CHAT,
    PLATFORM_LABELS,
    PLATFORMS,
    REDO,
    SUPABASE,
    TIKTOK_ADS,
    TIKTOK_CREATOR,
    TIKTOK_ORGANIC,
    TIKTOK_SHOP,
    WALMART,
)

Level = Literal["read", "write", "delete"]
Source = Literal["captured", "declared", "not_captured"]

LEVEL_LABELS: Final[dict[Level, str]] = {"read": "Lectura", "write": "Escritura", "delete": "Eliminación"}
WRITE_LEVELS: Final[frozenset[Level]] = frozenset({"write", "delete"})
UNDOCUMENTED_TEXT = "Sin documentar: revisar qué permite antes de dejarlo concedido."
UNDOCUMENTED_DAMAGE = "Se desconoce: trátalo como si pudiera modificar datos hasta confirmarlo."

# Regla de respaldo. Se compara contra los segmentos del nombre (separados por . _ - :), no contra subcadenas,
# para que `seller.product.writer_guide` o `overwrite` no se confundan con `write`.
DELETE_MARKERS: Final[frozenset[str]] = frozenset({"delete", "remove", "destroy"})
WRITE_MARKERS: Final[frozenset[str]] = frozenset({"write", "publish", "edit", "upload", "optimize", "manage", "create", "update"})
SEGMENT_SPLIT = re.compile(r"[._\-:]+")

PARTNER_CENTER_URL = "https://partner.tiktokshop.com"
# Los menús de Partner Center cambian de nombre; se describe la ruta general, no un clic exacto.
PARTNER_CENTER_STEPS: Final[tuple[str, ...]] = (
    f"Entra a Partner Center ({PARTNER_CENTER_URL}) > App & Service > tu app > permisos (scopes / API scope).",
    "Desmarca los permisos listados y guarda los cambios.",
    "Reautoriza la tienda con esa app: el token nuevo nace solo con los permisos que dejaste.",
    "Vuelve a ejecutar la captura (POST /api/security/scopes/capture/seller) para confirmar el resultado.",
)
CANNOT_REVOKE_FROM_APP = (
    "Esta aplicación no puede quitar permisos: la API de TikTok Shop no ofrece ningún endpoint para modificar "
    "los permisos de una app. Debe hacerse a mano en Partner Center."
)


@dataclass(frozen=True, slots=True)
class ScopeInfo:
    """Lo que se sabe de un permiso conocido."""

    platform: str
    level: Level
    allows: str  # qué permite, en español claro
    damage: str  # qué podría hacer alguien con un token filtrado
    used_by_app: bool  # ¿algún conector de la app lo necesita hoy?
    confirmed: bool = True  # False = clasificación conservadora, a verificar en el portal


@dataclass(frozen=True, slots=True)
class ScopeClassification:
    scope: str
    platform: str
    level: Level
    allows: str
    damage: str
    used_by_app: bool
    documented: bool
    confirmed: bool

    @property
    def is_write(self) -> bool:
        return self.level in WRITE_LEVELS

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "level": self.level,
            "level_label": LEVEL_LABELS[self.level],
            "allows": self.allows,
            "damage": self.damage,
            "used_by_app": self.used_by_app,
            "documented": self.documented,
            "confirmed": self.confirmed,
            "unused": not self.used_by_app,
        }


def _scope(platform: str, level: Level, allows: str, damage: str, used: bool, confirmed: bool = True) -> ScopeInfo:
    return ScopeInfo(platform, level, allows, damage, used, confirmed)


_SHOP = TIKTOK_SHOP
_ORG = TIKTOK_ORGANIC

KNOWN_SCOPES: Final[dict[str, ScopeInfo]] = {
    # --- TikTok Shop (vendedor): escritura y eliminación, que esta app NO usa ------------------------------
    "seller.product.write": _scope(
        _SHOP, "write", "Crear y editar productos, precios, inventario y descripciones de la tienda.",
        "Cambiar precios a $0,01, vaciar inventario o publicar productos falsos con tu marca.", False),
    "seller.product.delete": _scope(
        _SHOP, "delete", "Borrar o desactivar productos de la tienda.",
        "Eliminar el catálogo (y sus reseñas y ranking) en minutos.", False),
    "seller.global_product.write": _scope(
        _SHOP, "write", "Crear y editar productos globales (catálogo para varios países).",
        "Alterar listados y precios en todos los países a la vez.", False),
    "seller.global_product.delete": _scope(
        _SHOP, "delete", "Borrar productos globales.", "Eliminar el catálogo en todos los países.", False),
    "seller.affiliate_messages.write": _scope(
        _SHOP, "write", "Enviar mensajes a creadores afiliados en nombre de la tienda.",
        "Enviar spam o enlaces fraudulentos a tus creadores con tu nombre; dañar la relación con ellos.", False),
    "seller.fulfillment.package.write": _scope(
        _SHOP, "write", "Crear y enviar paquetes, y comprar o imprimir etiquetas de envío.",
        "Gastar saldo en etiquetas, marcar pedidos como enviados sin enviarlos o desviar paquetes.", False),
    "seller.delivery.status.write": _scope(
        _SHOP, "write", "Cambiar el estado de entrega de los pedidos.",
        "Marcar pedidos como entregados antes de tiempo: libera pagos y esconde envíos perdidos.", False),
    "seller.redeem_info.write": _scope(
        _SHOP, "write", "Registrar información de canje (cupones y vouchers) de pedidos.",
        "Alterar el canje de vouchers de clientes.", False),
    "seller.order.ext_ref.write": _scope(
        _SHOP, "write", "Escribir referencias externas en los pedidos.",
        "Corromper la conciliación con otros sistemas al cambiar las referencias.", False),
    "seller.affiliate_collaboration.write": _scope(
        _SHOP, "write", "Crear y modificar colaboraciones con creadores (invitaciones, comisiones, muestras).",
        "Ofrecer comisiones altas o enviar muestras a quien quiera: gasto directo.", False),
    "seller.promotion.write": _scope(
        _SHOP, "write", "Crear y editar promociones, descuentos y cupones.",
        "Lanzar descuentos del 99 % o cupones ilimitados: vender a pérdida.", False),
    "seller.global_product.image_trans_task": _scope(
        _SHOP, "write", "Crear tareas de traducción de imágenes de productos globales.",
        "Generar tareas (y posibles costos) y alterar las imágenes de tus listados.", False, confirmed=False),
    "serller.product.optimize": _scope(  # el nombre real del permiso trae esta errata de TikTok
        _SHOP, "write", "Aplicar optimizaciones y sugerencias de TikTok a los listados.",
        "Reescribir títulos, imágenes o atributos de tus productos sin que lo notes.", False),
    "seller.customer_service": _scope(
        _SHOP, "write", "Leer y responder conversaciones de servicio al cliente.",
        "Contestar a tus clientes con tu nombre o leer sus mensajes (datos personales).", False, confirmed=False),
    # --- TikTok Shop: lectura que la app usa ---------------------------------------------------------------
    "seller.authorization.info": _scope(
        _SHOP, "read", "Ver las tiendas autorizadas para la app.", "Saber qué tiendas existen (poco daño).", True),
    "seller.order.info": _scope(
        _SHOP, "read", "Leer pedidos, importes y datos de envío.", "Filtrar datos personales de clientes y ventas.", True),
    "seller.return_refund.basic": _scope(
        _SHOP, "read", "Leer devoluciones, reembolsos y cancelaciones.", "Filtrar datos de clientes y motivos de devolución.", True),
    "seller.product.basic": _scope(
        _SHOP, "read", "Leer el catálogo de productos y sus SKU.", "Copiar tu catálogo y precios.", True),
    "seller.finance.info": _scope(
        _SHOP, "read", "Leer liquidaciones, pagos y retiros.", "Filtrar tus finanzas y cuentas de pago.", True),
    "seller.promotion.info": _scope(
        _SHOP, "read", "Leer promociones y cupones.", "Ver tu estrategia de descuentos.", True),
    "seller.fulfillment.basic": _scope(
        _SHOP, "read", "Leer paquetes y seguimiento de envíos.", "Filtrar direcciones y datos de envío de clientes.", True),
    "seller.logistics": _scope(
        _SHOP, "read", "Leer almacenes y opciones logísticas.", "Filtrar direcciones de tus almacenes.", True),
    "seller.affiliate_collaboration.read": _scope(
        _SHOP, "read", "Leer solicitudes de muestras y pedidos de afiliados.", "Ver tus acuerdos con creadores.", True),
    "seller.shop.info": _scope(
        _SHOP, "read", "Leer datos de la tienda (nombre, región, estado).", "Ver datos básicos de la tienda.", False),
    "seller.order.ext_ref.read": _scope(
        _SHOP, "read", "Leer las referencias externas de los pedidos.", "Ver tus referencias internas de pedidos.", False),
    "seller.global_product.info": _scope(
        _SHOP, "read", "Leer productos globales (catálogo multi-país).", "Copiar tu catálogo global.", False),
    "seller.global_product.category.info": _scope(
        _SHOP, "read", "Leer categorías y atributos de productos globales.", "Ninguno relevante: datos de referencia.", False),
    "data.shop_analytics.public.read": _scope(
        _SHOP, "read", "Leer analítica de la tienda (rendimiento, videos, lives, productos).", "Ver el rendimiento de tu tienda.", True),
    # --- TikTok Shop: lectura que la app NO usa ------------------------------------------------------------
    "seller.creator_marketplace.read": _scope(
        _SHOP, "read", "Leer el mercado de creadores (búsqueda de creadores).", "Poco: datos públicos de creadores.", False),
    "seller.fbt.info": _scope(
        _SHOP, "read", "Leer inventario de Fulfilled by TikTok (FBT).", "Ver tu inventario en bodegas de TikTok.", False),
    "data.bestselling.public.read": _scope(
        _SHOP, "read", "Leer datos públicos de productos más vendidos.", "Ninguno relevante: datos públicos.", False),
    # --- TikTok orgánico (Login Kit + Display API): solo lectura -------------------------------------------
    "user.info.basic": _scope(_ORG, "read", "Leer nombre y foto del perfil.", "Ninguno relevante: datos públicos del perfil.", True),
    "user.info.profile": _scope(_ORG, "read", "Leer biografía, enlace y verificación del perfil.", "Ninguno relevante: datos públicos.", True),
    "user.info.stats": _scope(_ORG, "read", "Leer seguidores, likes y número de videos.", "Ver las métricas de la cuenta.", True),
    "video.list": _scope(_ORG, "read", "Listar los videos de la cuenta con sus métricas.", "Ver el rendimiento de tus videos.", True),
    "video.publish": _scope(
        _ORG, "write", "Publicar videos en la cuenta.", "Publicar contenido con tu cuenta sin tu permiso.", False),
    "video.upload": _scope(
        _ORG, "write", "Subir videos como borrador a la cuenta.", "Llenar la cuenta de borradores no autorizados.", False),
    # --- TikTok Creator: permisos del PERFIL (capacidades de la cuenta, no del token) ----------------------
    "LIVE_STREAM_PERMISSION": _scope(
        TIKTOK_CREATOR, "read", "La cuenta puede hacer transmisiones en vivo (capacidad de la cuenta).",
        "No es un permiso del token: solo indica lo que la cuenta puede hacer.", True, confirmed=False),
    "SELF_SALE_PERMISSION": _scope(
        TIKTOK_CREATOR, "read", "La cuenta puede vender sus propios productos (capacidad de la cuenta).",
        "No es un permiso del token: solo indica lo que la cuenta puede hacer.", True, confirmed=False),
    # --- Plataformas sin lista de permisos en su API: acceso declarado ------------------------------------
    "tiktok_ads.api_token": _scope(
        TIKTOK_ADS, "read", "La app solo consulta reportes de publicidad (el token no informa sus permisos).",
        "Si el token se creó con permisos de gestión, alguien podría cambiar presupuestos o pausar campañas: verifícalo.",
        True, confirmed=False),
    "walmart.api_credentials": _scope(
        WALMART, "read", "La app solo consulta pedidos, inventario, reportes y calidad de listados.",
        "Las credenciales de Walmart no se limitan por permiso: si se filtran pueden cambiar precios e inventario.",
        True, confirmed=False),
    "redo.api_key": _scope(
        REDO, "read", "La app solo consulta etiquetas, cargos y facturas.",
        "Una API key de Redo suele poder más que consultar: verifica que sea de solo lectura.", True, confirmed=False),
    "supabase.publishable_key": _scope(
        SUPABASE, "read", "Clave publicable del frontend: solo lo que permiten las políticas RLS.",
        "Está pensada para ser pública; el riesgo lo limitan las políticas de seguridad por fila.", True),
    "supabase.database_connection": _scope(
        SUPABASE, "delete", "Cadena de conexión del backend: acceso TOTAL a la base (leer, escribir, borrar y alterar).",
        "Quien la tenga puede leer todo, borrar tablas y saltarse las políticas RLS.", True),
}

# Permisos que se saben concedidos sin consultar a la plataforma (verificados en las pruebas).
DECLARED_SCOPES: Final[dict[str, tuple[str, ...]]] = {
    TIKTOK_ORGANIC: ("user.info.basic", "user.info.profile", "user.info.stats", "video.list"),
    TIKTOK_CREATOR: ("LIVE_STREAM_PERMISSION", "SELF_SALE_PERMISSION"),
    TIKTOK_ADS: ("tiktok_ads.api_token",),
    WALMART: ("walmart.api_credentials",),
    REDO: ("redo.api_key",),
    SUPABASE: ("supabase.publishable_key", "supabase.database_connection"),
}
CAPTURABLE_PLATFORMS: Final[tuple[str, ...]] = (TIKTOK_SHOP,)  # su API devuelve los permisos al renovar el token


def _fallback_level(scope: str) -> Level:
    segments = set(SEGMENT_SPLIT.split(scope.lower()))
    if segments & DELETE_MARKERS:
        return "delete"
    if segments & WRITE_MARKERS:
        return "write"
    return "read"


def classify_scope(scope: str, platform: str = "unknown") -> ScopeClassification:
    """Clasifica un permiso: el conocido tal cual; el desconocido por patrón y marcado "sin documentar"."""
    known = KNOWN_SCOPES.get(scope)
    if known is not None:
        return ScopeClassification(
            scope, known.platform, known.level, known.allows, known.damage, known.used_by_app, True, known.confirmed
        )
    return ScopeClassification(
        scope, platform, _fallback_level(scope), UNDOCUMENTED_TEXT, UNDOCUMENTED_DAMAGE, False, False, False
    )


def _names(items: Iterable[ScopeClassification]) -> list[str]:
    return sorted(i.scope for i in items)


def recommendation(platform: str, scopes: tuple[ScopeClassification, ...], source: Source) -> dict[str, Any]:
    """Qué hacer con los permisos de la plataforma. Solo TikTok Shop tiene permisos que se pueden quitar a mano."""
    if source == "not_captured":
        return {
            "action": "capture",
            "summary": "Aún no se capturaron los permisos concedidos: ejecuta la captura segura.",
            "remove_now": [],
            "remove_optional": [],
            "review": [],
            "steps": [],
            "can_revoke_from_app": False,
        }
    unused_write = [s for s in scopes if s.is_write and not s.used_by_app]
    unused_read = [s for s in scopes if not s.is_write and not s.used_by_app]
    review = [s for s in scopes if not s.documented or not s.confirmed]
    if platform == TIKTOK_SHOP:
        action = "remove" if unused_write else ("trim" if unused_read else "none")
        summary = (
            f"Quita {len(unused_write)} permisos de escritura/eliminación en Partner Center: esta app solo lee. "
            + (f"Después puedes quitar {len(unused_read)} de lectura que tampoco usa. " if unused_read else "")
            + CANNOT_REVOKE_FROM_APP
            if unused_write
            else (
                f"No hay permisos de escritura. Opcional: quita {len(unused_read)} de lectura que la app no usa."
                if unused_read
                else "Los permisos concedidos coinciden con lo que la app usa."
            )
        )
        return {
            "action": action,
            "summary": summary,
            "remove_now": _names(unused_write),
            "remove_optional": _names(unused_read),
            "review": _names(review),
            "steps": list(PARTNER_CENTER_STEPS) if unused_write or unused_read else [],
            "can_revoke_from_app": False,
        }
    wide = [s for s in scopes if s.is_write]
    return {
        "action": "verify" if review or wide else "none",
        "summary": _declared_summary(platform, wide, review),
        "remove_now": [],
        "remove_optional": [],
        "review": _names(review),
        "steps": [],
        "can_revoke_from_app": False,
    }


def _declared_summary(platform: str, wide: list[ScopeClassification], review: list[ScopeClassification]) -> str:
    if platform == SUPABASE:
        return (
            "La clave publicable del frontend es segura (la limitan las políticas RLS), pero la cadena de conexión del "
            "backend tiene acceso TOTAL a la base. Considera un rol de base de datos con los mínimos privilegios."
        )
    if review:
        return "La plataforma no informa sus permisos: verifica en su portal que el token sea de solo lectura."
    return "Solo lectura: nada que quitar."


def platform_scopes(
    platform: str, granted: tuple[str, ...] | None, captured_at: datetime | None
) -> dict[str, Any]:
    """Permisos de una plataforma: fuente, clasificación, conteos y recomendación.

    `granted` es la foto capturada (None = aún no capturada); si la plataforma no enumera permisos, se usan los
    declarados. Una plataforma capturable sin foto queda `not_captured`.
    """
    if granted is not None:
        source: Source = "captured"
    elif platform in DECLARED_SCOPES:
        source, granted = "declared", DECLARED_SCOPES[platform]
    else:
        source, granted = "not_captured", ()
    classified = tuple(sorted((classify_scope(s, platform) for s in granted), key=lambda c: (c.level == "read", c.scope)))
    unused = [c for c in classified if not c.used_by_app]
    return {
        "platform": platform,
        "label": PLATFORM_LABELS[platform],
        "source": source,
        "captured_at": captured_at.isoformat() if captured_at else None,
        "capturable": platform in CAPTURABLE_PLATFORMS,
        "granted": len(classified),
        "write_count": sum(c.is_write for c in classified),
        "delete_count": sum(c.level == "delete" for c in classified),
        "unused_count": len(unused),
        "unused_write_count": sum(c.is_write for c in unused),
        "undocumented_count": sum(not c.documented for c in classified),
        "scopes": [c.to_dict() for c in classified],
        "recommendation": recommendation(platform, classified, source),
    }


def scope_report(snapshots: Mapping[str, tuple[tuple[str, ...], datetime | None]]) -> list[dict[str, Any]]:
    """Permisos de todas las plataformas con permisos (en el orden del catálogo). `snapshots`: plataforma -> (nombres, fecha)."""
    platforms = [p for p in PLATFORMS if p in snapshots or p in DECLARED_SCOPES or p in CAPTURABLE_PLATFORMS]
    return [platform_scopes(p, *snapshots.get(p, (None, None))) for p in platforms]


# --- resumen y riesgo global -------------------------------------------------------------------------------
EXPIRY_WARNING_DAYS = 30  # un refresh token que vence en menos de esto exige reautorizar pronto

RiskLevel = Literal["low", "medium", "high"]


def pending_rotation(
    rotated: set[str], present: frozenset[str], exposed: frozenset[str] = EXPOSED_IN_CHAT
) -> list[str]:
    """Plataformas pendientes de rotar: las expuestas en el chat y cualquier otra con credenciales presentes."""
    return [p for p in PLATFORMS if (p in exposed or p in present) and p not in rotated]


def upcoming_expirations(inventory: list[dict[str, Any]], within_days: float = EXPIRY_WARNING_DAYS) -> list[dict[str, Any]]:
    """Tokens de renovación presentes que vencieron o vencen pronto (el de acceso se renueva solo: no se avisa)."""
    rows = [
        {"platform": i["platform"], "name": i["name"], "expires_at": i["expires_at"], "days_left": i["days_left"]}
        for i in inventory
        if i["present"] and i["kind"] == "refresh_token" and i["days_left"] is not None and i["days_left"] <= within_days
    ]
    return sorted(rows, key=lambda r: r["days_left"])


def assess_risk(
    *, pending: list[str], write_platforms: list[str], unignored_files: list[str], expiring: list[dict[str, Any]]
) -> dict[str, str]:
    """Riesgo global con su razón.

    alto: un archivo con secretos que git no ignora (puede subirse a un repositorio), o un secreto expuesto
          sin rotar en una plataforma con permisos de escritura sobrantes (filtración + capacidad de daño).
    medio: secretos expuestos sin rotar, permisos de escritura sobrantes o un token de renovación por vencer.
    bajo: nada de lo anterior.
    """
    exposed_with_write = sorted(set(pending) & set(write_platforms))
    if unignored_files:
        return {"level": "high", "reason": f"Archivos con secretos que git NO ignora: {', '.join(unignored_files)}."}
    if exposed_with_write:
        labels = ", ".join(PLATFORM_LABELS[p] for p in exposed_with_write)
        return {
            "level": "high",
            "reason": f"Secretos expuestos sin rotar en plataformas con permisos de escritura sobrantes: {labels}.",
        }
    reasons: list[str] = []
    if pending:
        reasons.append(f"{len(pending)} plataformas con secretos expuestos sin rotar")
    if write_platforms:
        reasons.append("permisos de escritura sobrantes")
    if expiring:
        reasons.append(f"{len(expiring)} tokens de renovación vencen pronto o ya vencieron")
    if reasons:
        return {"level": "medium", "reason": "; ".join(reasons).capitalize() + "."}
    return {"level": "low", "reason": "Sin secretos pendientes de rotar ni permisos de escritura sobrantes."}


def build_overview(
    scopes: list[dict[str, Any]], inventory: list[dict[str, Any]], pending: list[str]
) -> dict[str, Any]:
    """Resumen para la pantalla de seguridad: permisos de escritura, rotaciones pendientes, vencimientos y riesgo."""
    write_platforms = [s["platform"] for s in scopes if s["unused_write_count"] > 0]
    unignored = sorted({i["file"] for i in inventory if i["present"] and i["git_ignored"] is False})
    unignored += sorted(
        {c["file"] for i in inventory for c in i["copies"] if c["git_ignored"] is False} - set(unignored)
    )
    expiring = upcoming_expirations(inventory)
    return {
        "write_scopes_granted": sum(s["write_count"] for s in scopes),
        "unused_write_scopes": sum(s["unused_write_count"] for s in scopes),
        "platforms_with_unused_write": write_platforms,
        "pending_rotation": pending,
        "pending_rotation_count": len(pending),
        "upcoming_expirations": expiring,
        "files_not_git_ignored": unignored,
        "risk": assess_risk(pending=pending, write_platforms=write_platforms, unignored_files=unignored, expiring=expiring),
    }
