"""Runtime configuration, loaded from environment variables.

No credential is ever hardcoded: every secret is read from the environment, and
``.env`` is excluded from version control. ``.env.example`` documents the shape
of the configuration without carrying any real value.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Application
    project_name: str = "SaludRed - Coordinacion de camas EPS/IPS"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"

    # Database
    database_url: str = Field(
        ..., description="SQLAlchemy URL for the application PostgreSQL database"
    )
    database_echo: bool = False

    # Authentication
    jwt_secret_key: str = Field(..., min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # FHIR integration
    fhir_base_url: str = "http://localhost:8080/fhir"
    fhir_identifier_system: str = "urn:saludred:identifier"
    fhir_request_timeout_seconds: int = 30

    # Seeding
    seed_default_password: str = "Demo2026!"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance.

    Cached so configuration is parsed once per process instead of on every
    request, and so tests can override it by clearing the cache.
    """

    return Settings()  # type: ignore[call-arg]
