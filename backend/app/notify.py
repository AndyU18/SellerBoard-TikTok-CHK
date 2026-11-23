"""Envío del resumen por correo, Slack o webhook genérico, y programador del envío automático.

Privacidad: el resumen lleva cifras del negocio a servicios externos, por eso `settings.digest_enabled`
es False por defecto y el programador no hace nada hasta que el usuario lo active y configure un canal.
El envío de prueba es siempre una acción explícita del usuario.

Decisiones:
- Cada canal se intenta de forma independiente: un fallo no impide los demás.
- Cada intento deja una fila en `ops.notification_log`. En `detail` solo va el tipo de error (y el
  código HTTP/SMTP), nunca URLs, contraseñas ni tokens; además se redacta cualquier secreto conocido.
- `digest_due` es puro respecto al reloj (recibe `now`) y consulta el registro, así que varios procesos
  o reinicios del backend no duplican envíos (mismo patrón que `sync_jobs`).
- Los transportes (cliente httpx, fábrica de SMTP) se pueden inyectar para las pruebas.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.digest import build_digest, render_html, render_slack, render_text
from app.models import NotificationLog

log = logging.getLogger("chk.notify")

CHANNELS = ("email", "slack", "webhook")
HTTP_TIMEOUT_SECONDS = 15.0
SMTP_TIMEOUT_SECONDS = 20
CHECK_SECONDS = 600  # el programador revisa cada 10 minutos
RETRY_AFTER = timedelta(hours=1)  # tras un intento fallido no se reintenta antes de esto
DETAIL_MAX = 200
REDACTED = "[redactado]"
_URL = re.compile(r"https?://\S+")

SmtpFactory = Callable[..., Any]


def configured_channels() -> dict[str, bool]:
    """Qué canales tienen la configuración mínima. El correo exige servidor, remitente y destinatarios."""
    return {
        "email": bool(settings.smtp_host and settings.smtp_from and settings.smtp_to),
        "slack": bool(settings.slack_webhook_url),
        "webhook": bool(settings.digest_webhook_url),
    }


def _secrets() -> list[str]:
    values = [
        settings.smtp_password,
        settings.smtp_user,
        settings.slack_webhook_url,
        settings.digest_webhook_url,
        settings.smtp_host,
        *settings.smtp_to,
    ]
    return [v for v in values if v]


def _redact(text: str) -> str:
    """Quita URLs y cualquier valor secreto de la configuración de un texto."""
    for secret in _secrets():
        text = text.replace(secret, REDACTED)
    return _URL.sub(REDACTED, text)[:DETAIL_MAX]


def _describe(exc: BaseException) -> str:
    """Descripción segura de un error: su tipo y, si lo hay, el código HTTP o SMTP."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__}: HTTP {exc.response.status_code}"
    code = getattr(exc, "smtp_code", None)
    return _redact(f"{type(exc).__name__}: código {code}" if code else type(exc).__name__)


# ---------------------------------------------------------------------------- canales


def _send_email_sync(message: EmailMessage, factory: SmtpFactory | None) -> None:
    if factory is None:
        factory = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
    with factory(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        if settings.smtp_starttls and settings.smtp_port != 465:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_email(
    subject: str, text: str, html_body: str, *, smtp_factory: SmtpFactory | None = None
) -> None:
    """Envía el correo (texto + HTML) a `smtp_to` sin bloquear el bucle de eventos."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or ""
    message["To"] = ", ".join(settings.smtp_to)
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    await asyncio.to_thread(_send_email_sync, message, smtp_factory)


async def _post_json(url: str, payload: dict[str, Any], client: httpx.AsyncClient | None) -> None:
    if client is not None:
        response = await client.post(url, json=payload)
    else:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as own:
            response = await own.post(url, json=payload)
    response.raise_for_status()


async def send_slack(text: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Publica el resumen (mrkdwn) en el webhook entrante de Slack."""
    await _post_json(settings.slack_webhook_url or "", {"text": text}, client)


async def send_webhook(payload: dict[str, Any], *, client: httpx.AsyncClient | None = None) -> None:
    """POST JSON genérico; sirve para WhatsApp vía Twilio, Make, Zapier u otro servicio externo."""
    await _post_json(settings.digest_webhook_url or "", payload, client)


# ---------------------------------------------------------------------------- entrega


def _title(kind: str, digest: dict[str, Any]) -> str:
    return f"[Prueba] {digest['label']}" if kind == "test" else digest["label"]


async def deliver(
    session: AsyncSession,
    kind: str,
    digest: dict[str, Any],
    channels: Iterable[str] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    smtp_factory: SmtpFactory | None = None,
) -> dict[str, dict[str, str]]:
    """Entrega el resumen a cada canal configurado (o a los indicados) y registra cada intento.

    Devuelve `{canal: {"status": "sent"|"failed"|"skipped", "detail": ...}}`. No hace commit: lo hace
    quien llama. Un canal pedido pero no configurado queda como `skipped`.
    """
    configured = configured_channels()
    wanted = [c for c in CHANNELS if channels is None or c in set(channels)]
    title = _title(kind, digest)
    text = render_text(digest)
    results: dict[str, dict[str, str]] = {}

    for channel in wanted:
        if not configured[channel]:
            if channels is not None:
                results[channel] = {"status": "skipped", "detail": "canal no configurado"}
            continue
        try:
            if channel == "email":
                await send_email(title, text, render_html(digest), smtp_factory=smtp_factory)
            elif channel == "slack":
                await send_slack(f"*{title}*\n{render_slack(digest)}" if kind == "test" else render_slack(digest), client=client)
            else:
                await send_webhook(
                    {"kind": kind, "title": title, "text": text, "digest": digest}, client=client
                )
            results[channel] = {"status": "sent", "detail": ""}
        except Exception as exc:  # noqa: BLE001 - un canal caído no debe frenar a los demás
            results[channel] = {"status": "failed", "detail": _describe(exc)}
            log.warning("Envío del resumen por %s falló: %s", channel, results[channel]["detail"])

    for channel, result in results.items():
        session.add(
            NotificationLog(kind=kind, channel=channel, status=result["status"], detail=result["detail"] or None)
        )
    await session.flush()
    return results


# ---------------------------------------------------------------------------- programador


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def digest_due(session: AsyncSession, now: datetime | None = None) -> list[str]:
    """Resúmenes que toca enviar ahora: `daily` y/o `weekly`.

    Toca si ya pasó la hora local configurada y todavía no hay un envío exitoso de ese tipo en el día
    local. El semanal solo sale en su día de la semana. Tras un intento fallido se espera `RETRY_AFTER`.
    """
    now = _aware(now or datetime.now(UTC))
    zone = ZoneInfo(settings.report_timezone)
    local = now.astimezone(zone)
    if local.hour < settings.digest_daily_hour:
        return []

    kinds = ["daily"]
    if local.weekday() == settings.digest_weekly_weekday:
        kinds.append("weekly")

    recent = (
        await session.scalars(
            select(NotificationLog)
            .where(NotificationLog.kind.in_(kinds), NotificationLog.status.in_(("sent", "failed")))
            .where(NotificationLog.created_at >= now - timedelta(days=2))
        )
    ).all()

    due = []
    for kind in kinds:
        entries = [r for r in recent if r.kind == kind]
        sent_today = any(
            r.status == "sent" and _aware(r.created_at).astimezone(zone).date() == local.date()
            for r in entries
        )
        failed_recently = any(
            r.status == "failed" and now - _aware(r.created_at) < RETRY_AFTER for r in entries
        )
        if not sent_today and not failed_recently:
            due.append(kind)
    return due


async def send_due(factory: async_sessionmaker[AsyncSession], now: datetime | None = None) -> dict[str, Any]:
    """Construye y entrega los resúmenes que toquen. Devuelve lo enviado por tipo."""
    if not settings.digest_enabled or not any(configured_channels().values()):
        return {}
    out: dict[str, Any] = {}
    async with factory() as session:
        for kind in await digest_due(session, now):
            digest = await build_digest(session, kind)  # type: ignore[arg-type]
            out[kind] = await deliver(session, kind, digest)
            await session.commit()
    return out


async def digest_loop(factory: async_sessionmaker[AsyncSession]) -> None:
    """Tarea de fondo del resumen automático. No hace nada si está apagado; se cancela al apagar."""
    while True:
        try:
            sent = await send_due(factory)
            if sent:
                log.info("Resumen automático enviado: %s", {k: list(v) for k, v in sent.items()})
        except Exception:
            log.exception("Fallo inesperado en el resumen automático")
        await asyncio.sleep(CHECK_SECONDS)
