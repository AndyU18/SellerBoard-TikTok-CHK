from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variables de entorno con prefijo CHK_ (o archivo .env)."""

    # Desarrollo local: SQLite. Producción: postgresql+asyncpg://user:pass@host/db
    database_url: str = "sqlite+aiosqlite:///./chk_dev.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_upload_bytes: int = 20 * 1024 * 1024

    # extra="ignore": el mismo .env guarda variables TTS_*/TTADS_* de los conectores.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CHK_", extra="ignore")


settings = Settings()
