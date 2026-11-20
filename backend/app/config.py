from pydantic import field_validator
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

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_upload_bytes: int = 20 * 1024 * 1024

    # extra="ignore": el mismo .env guarda variables TTS_*/TTADS_* de los conectores.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHK_", extra="ignore")


settings = Settings()
