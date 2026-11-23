from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variables de entorno con prefijo CHK_ (o archivo .env)."""

    # Desarrollo local: SQLite. Producción: la cadena de conexión de Supabase
    # (postgresql://... se convierte sola al driver asyncpg).
    database_url: str = "sqlite+aiosqlite:///./chk_local.db"

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, url: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix) :]
        return url

    # Autenticación (Supabase Auth). La URL ya vive en .env como SUPABASE_URL.
    supabase_url: str | None = Field(
        default=None, validation_alias=AliasChoices("CHK_SUPABASE_URL", "SUPABASE_URL")
    )
    # Solo para desarrollo local sin login: CHK_AUTH_REQUIRED=false da acceso total. NUNCA en producción.
    auth_required: bool = True

    # Zona horaria para los reportes por hora (dayparting). Las horas se guardan en UTC.
    report_timezone: str = "America/New_York"

    # Sincronización automática de las plataformas (solo lectura). CHK_AUTO_SYNC=false la apaga.
    auto_sync_enabled: bool = Field(
        default=True, validation_alias=AliasChoices("CHK_AUTO_SYNC", "CHK_AUTO_SYNC_ENABLED")
    )
    auto_sync_interval_minutes: int = 360  # cada cuánto se sincroniza cada fuente
    auto_sync_days: int = 14  # ventana que se vuelve a leer en cada ejecución
    auto_sync_check_seconds: int = 600  # cada cuánto el programador revisa qué toca
    auto_sync_start_delay_seconds: int = 90  # espera tras arrancar el backend

    # Resumen diario/semanal. TODO apagado por defecto: nada sale de la aplicación hasta que
    # CHK_DIGEST_ENABLED=true y se configure al menos un canal (correo, Slack o webhook).
    digest_enabled: bool = False
    digest_daily_hour: int = 8  # hora local (CHK_REPORT_TIMEZONE) del resumen diario
    digest_weekly_weekday: int = 0  # 0 = lunes; el resumen semanal sale ese día a la misma hora
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    smtp_from: str | None = None
    smtp_to: list[str] = []
    slack_webhook_url: str | None = None
    digest_webhook_url: str | None = None  # POST JSON genérico (WhatsApp vía Twilio/Make/Zapier, etc.)

    # Asistente con IA (API de Anthropic). Sin clave no se envía nada. Cuando se usa, las cifras de la
    # pregunta (ventas, costos, SKU) viajan a Anthropic; no se guardan datos de clientes en la aplicación.
    ai_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("CHK_AI_API_KEY", "ANTHROPIC_API_KEY")
    )
    ai_model: str = "claude-sonnet-5-5"
    ai_max_tokens: int = 1500

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_upload_bytes: int = 20 * 1024 * 1024

    # extra="ignore": el mismo .env guarda variables TTS_*/TTADS_* de los conectores.
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CHK_", extra="ignore"
    )


settings = Settings()
