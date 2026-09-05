"""
Centralized app configuration.

Loads settings from environment variables (see .env.example).
No business logic here — just config plumbing so routes/services/database
modules have a single source of truth for things like DB URL, env name, etc.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Smart Market Watchlist API"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True

    # Database — always set DATABASE_URL in .env; this fallback is only here so
    # Settings() doesn't crash if .env is missing, and intentionally uses no real password.
    database_url: str = "postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/smart_market_watchlist"

    # CORS — frontend origin(s), comma-separated in .env
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if "YOUR_PASSWORD" in value or not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must be a configured PostgreSQL psycopg connection string")
        return value

    @model_validator(mode="after")
    def validate_production_debug(self):
        if self.environment == "production" and self.debug:
            raise ValueError("DEBUG must be false when ENVIRONMENT=production")
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import and call this, don't instantiate Settings() directly."""
    return Settings()
