from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Commercial SaaS Core"
    ENVIRONMENT: str = "development"
    POSTGRES_USER: str = "saas_admin"
    POSTGRES_PASSWORD: str = "saas_super_secret_password_2026"
    POSTGRES_DB: str = "commercial_saas_db"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    DATABASE_URL: str = (
        "postgresql+asyncpg://saas_admin:saas_super_secret_password_2026@db:5432/commercial_saas_db"
    )
    REDIS_URL: str = "redis://redis:6379/0"


settings = Settings()
