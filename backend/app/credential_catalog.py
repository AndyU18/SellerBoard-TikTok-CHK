"""Catálogo estático de credenciales: qué plataformas hay, cómo se llaman sus credenciales y cómo se rotan.

SOLO NOMBRES. Aquí no hay ni puede haber un valor: son nombres de variables de entorno, nombres de archivos de
tokens, nombres de campos de esos archivos y enlaces a los portales. El inventario (`credential_inventory`)
mira si cada una existe; la auditoría (`credential_audit`) clasifica los permisos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

CredentialKind = Literal["secret_key", "access_token", "refresh_token", "connection_string", "webhook_url"]
Location = Literal["env", "file"]

KIND_LABELS: Final[dict[CredentialKind, str]] = {
    "secret_key": "Clave secreta",
    "access_token": "Token de acceso",
    "refresh_token": "Token de renovación",
    "connection_string": "Cadena de conexión",
    "webhook_url": "URL de webhook (quien la tenga puede publicar)",
}

# --- plataformas -------------------------------------------------------------------------------------------
TIKTOK_SHOP: Final = "tiktok_shop"
TIKTOK_CREATOR: Final = "tiktok_creator"
TIKTOK_ORGANIC: Final = "tiktok_organic"
TIKTOK_ADS: Final = "tiktok_ads"
WALMART: Final = "walmart"
REDO: Final = "redo"
SUPABASE: Final = "supabase"
ANTHROPIC: Final = "anthropic"
SMTP: Final = "smtp"
SLACK: Final = "slack"
DIGEST_WEBHOOK: Final = "digest_webhook"

PLATFORM_LABELS: Final[dict[str, str]] = {
    TIKTOK_SHOP: "TikTok Shop (vendedor)",
    TIKTOK_CREATOR: "TikTok Creator",
    TIKTOK_ORGANIC: "TikTok orgánico",
    TIKTOK_ADS: "TikTok Ads",
    WALMART: "Walmart",
    REDO: "Redo",
    SUPABASE: "Supabase (base de datos)",
    ANTHROPIC: "IA (Anthropic)",
    SMTP: "Correo (SMTP)",
    SLACK: "Slack",
    DIGEST_WEBHOOK: "Webhook del resumen",
}
PLATFORMS: Final[tuple[str, ...]] = tuple(PLATFORM_LABELS)

# Plataformas cuyos secretos se compartieron en el chat con el asistente: se consideran comprometidos y
# PENDIENTES DE ROTAR hasta que alguien las marque (ops.credential_rotation).
EXPOSED_IN_CHAT: Final[frozenset[str]] = frozenset(
    {TIKTOK_SHOP, TIKTOK_CREATOR, TIKTOK_ORGANIC, TIKTOK_ADS, WALMART, REDO, SUPABASE}
)


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    """Una credencial conocida. `name` es la variable de entorno, o el archivo + el campo si `location` es file."""

    platform: str
    name: str
    kind: CredentialKind
    location: Location
    file: str  # .env o el archivo de tokens (relativo a backend/)
    field: str | None = None  # campo dentro del archivo de tokens
    expiry_field: str | None = None  # campo con la fecha de vencimiento (solo la fecha se lee)


ENV_FILE = ".env"
SELLER_TOKENS = "tts_tokens.json"
CREATOR_TOKENS = "tokens_creator.json"
ORGANIC_TOKENS = "tiktok_organic_tokens.json"
TOKEN_FILES: Final[tuple[str, ...]] = (SELLER_TOKENS, CREATOR_TOKENS, ORGANIC_TOKENS)


def _env(platform: str, name: str, kind: CredentialKind) -> CredentialSpec:
    return CredentialSpec(platform, name, kind, "env", ENV_FILE)


def _tokens(platform: str, file: str, access_expiry: str, refresh_expiry: str) -> tuple[CredentialSpec, ...]:
    return (
        CredentialSpec(platform, f"{file}:access_token", "access_token", "file", file, "access_token", access_expiry),
        CredentialSpec(platform, f"{file}:refresh_token", "refresh_token", "file", file, "refresh_token", refresh_expiry),
    )


CREDENTIAL_SPECS: Final[tuple[CredentialSpec, ...]] = (
    _env(TIKTOK_SHOP, "TTS_APP_SECRET", "secret_key"),
    *_tokens(TIKTOK_SHOP, SELLER_TOKENS, "access_token_expire_in", "refresh_token_expire_in"),
    # La cuenta de creador usa la MISMA app (TTS_APP_SECRET) pero tokens propios.
    *_tokens(TIKTOK_CREATOR, CREATOR_TOKENS, "access_token_expire_in", "refresh_token_expire_in"),
    _env(TIKTOK_ORGANIC, "TIKTOK_ORGANIC_CLIENT_SECRET", "secret_key"),
    _env(TIKTOK_ORGANIC, "TIKTOK_ORGANIC_SANDBOX_CLIENT_SECRET", "secret_key"),
    _env(TIKTOK_ORGANIC, "TIKTOK_ORGANIC_PROD_CLIENT_SECRET", "secret_key"),
    *_tokens(TIKTOK_ORGANIC, ORGANIC_TOKENS, "access_expires_at", "refresh_expires_at"),
    _env(TIKTOK_ADS, "TTADS_APP_SECRET", "secret_key"),
    _env(TIKTOK_ADS, "TTADS_ACCESS_TOKEN", "access_token"),
    _env(WALMART, "WMT_CLIENT_SECRET", "secret_key"),
    _env(REDO, "REDO_API_KEY", "secret_key"),
    _env(SUPABASE, "CHK_DATABASE_URL", "connection_string"),
    _env(ANTHROPIC, "CHK_AI_API_KEY", "secret_key"),
    _env(ANTHROPIC, "ANTHROPIC_API_KEY", "secret_key"),
    _env(SMTP, "CHK_SMTP_PASSWORD", "secret_key"),
    _env(SLACK, "CHK_SLACK_WEBHOOK_URL", "webhook_url"),
    _env(DIGEST_WEBHOOK, "CHK_DIGEST_WEBHOOK_URL", "webhook_url"),
)

# --- cómo rotar --------------------------------------------------------------------------------------------
CLOSING_STEPS: Final[tuple[str, ...]] = (
    "Borra las copias viejas del valor (.env.bak, .env.pre-*.bak, tts_tokens.json.bak...): también contienen el secreto expuesto.",
    "Reinicia el backend y comprueba que la sincronización de la plataforma sigue funcionando.",
    "Marca la plataforma como rotada en esta pantalla (PUT /api/security/rotation/{plataforma}).",
)


@dataclass(frozen=True, slots=True)
class RotationGuide:
    portal_url: str
    steps: tuple[str, ...]


ROTATION_GUIDES: Final[dict[str, RotationGuide]] = {
    TIKTOK_SHOP: RotationGuide(
        "https://partner.tiktokshop.com",
        (
            "En Partner Center > App & Service > tu app, regenera el App Secret.",
            "Copia el secreto nuevo a TTS_APP_SECRET en backend/.env (sin pegarlo en chats ni commits).",
            "Reautoriza la tienda para obtener tokens nuevos (los anteriores quedan invalidados) y reemplaza backend/tts_tokens.json.",
        ),
    ),
    TIKTOK_CREATOR: RotationGuide(
        "https://partner.tiktokshop.com",
        (
            "El App Secret es el de TikTok Shop (TTS_APP_SECRET): rótalo primero allí.",
            "Reautoriza la cuenta de creador desde cero: el refresh token es el único y no se puede recuperar.",
            "Guarda los tokens nuevos en backend/tokens_creator.json (la renovación atómica deja el respaldo .bak).",
        ),
    ),
    TIKTOK_ORGANIC: RotationGuide(
        "https://developers.tiktok.com/apps",
        (
            "En TikTok for Developers > tu app, regenera el Client Secret del entorno sandbox y el de producción.",
            "Actualiza TIKTOK_ORGANIC_CLIENT_SECRET, TIKTOK_ORGANIC_SANDBOX_CLIENT_SECRET y TIKTOK_ORGANIC_PROD_CLIENT_SECRET en backend/.env.",
            "Vuelve a autorizar la cuenta (flujo OAuth de la app) para regenerar backend/tiktok_organic_tokens.json.",
        ),
    ),
    TIKTOK_ADS: RotationGuide(
        "https://business-api.tiktok.com/portal/apps",
        (
            "En TikTok API for Business > My Apps > tu app, regenera el Secret.",
            "Vuelve a autorizar la cuenta publicitaria para emitir un access token nuevo.",
            "Actualiza TTADS_APP_SECRET y TTADS_ACCESS_TOKEN en backend/.env.",
        ),
    ),
    WALMART: RotationGuide(
        "https://developer.walmart.com",
        (
            "En Walmart Developer Portal > Key Management, genera una clave nueva (Client Secret) y revoca la anterior.",
            "Actualiza WMT_CLIENT_SECRET en backend/.env.",
        ),
    ),
    REDO: RotationGuide(
        "https://www.getredo.com",
        (
            "En el panel de Redo (Ajustes > API / integraciones), genera una API key nueva y revoca la anterior.",
            "Actualiza REDO_API_KEY en backend/.env.",
        ),
    ),
    SUPABASE: RotationGuide(
        "https://supabase.com/dashboard/project/qtmelerdcgxodllulnam/settings/database",
        (
            "En Supabase > Project Settings > Database, restablece la contraseña de la base de datos.",
            "Actualiza la contraseña dentro de CHK_DATABASE_URL en backend/.env (y en el entorno de despliegue).",
            "Revisa que ningún otro servicio use la contraseña vieja.",
        ),
    ),
    ANTHROPIC: RotationGuide(
        "https://console.anthropic.com/settings/keys",
        ("Crea una API key nueva en la consola, actualiza CHK_AI_API_KEY (o ANTHROPIC_API_KEY) y borra la anterior.",),
    ),
    SMTP: RotationGuide(
        "https://myaccount.google.com/apppasswords",
        ("Cambia la contraseña (o contraseña de aplicación) en tu proveedor de correo y actualiza CHK_SMTP_PASSWORD.",),
    ),
    SLACK: RotationGuide(
        "https://api.slack.com/apps",
        ("En Slack > tu app > Incoming Webhooks, revoca la URL y crea una nueva; actualiza CHK_SLACK_WEBHOOK_URL.",),
    ),
    DIGEST_WEBHOOK: RotationGuide(
        "https://www.make.com",
        ("Regenera la URL del webhook en el servicio que la emite (Make, Zapier, Twilio...) y actualiza CHK_DIGEST_WEBHOOK_URL.",),
    ),
}
