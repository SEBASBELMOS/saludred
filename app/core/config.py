"""Runtime configuration, loaded from environment variables.

No credential is ever hardcoded: every secret is read from the environment, and
``.env`` is excluded from version control. ``.env.example`` documents the shape
of the configuration without carrying any real value.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
        ...,
        validation_alias=AliasChoices("DATABASE_URL", "PG_CONNECTION_STRING"),
        description="SQLAlchemy URL for the application PostgreSQL database",
    )
    database_echo: bool = False

    # Authentication
    jwt_secret_key: str = Field(..., min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Pseudonymization (R12). HMAC key that turns a patient id into the opaque
    # identifier the AI service sees. Required like the JWT key: with a
    # guessable key anyone could recompute the pseudonym of a known id. Changing
    # it changes every pseudonym, so it is generated once per environment.
    pseudonym_key: str = Field(..., min_length=16)

    # AI service (own container). Only the API talks to it, by service name on
    # the internal Docker network. The cleaning prediction is secondary to the
    # bed move, so it gets a shorter timeout.
    ml_service_url: str = "http://ml:8001"
    ml_timeout_seconds: float = 8.0
    ml_cleaning_timeout_seconds: float = 2.0

    # FHIR integration
    fhir_base_url: str = "http://localhost:8080/fhir"
    fhir_identifier_system: str = "urn:saludred:identifier"
    fhir_request_timeout_seconds: int = 30
    # R15: push patients (and encounters, observations) to HAPI right after the
    # clinical write, in a background task. A HAPI outage never fails the write.
    fhir_auto_sync: bool = True
    fhir_auto_sync_timeout_seconds: int = 10

    # R21: HAPI Subscription (rest-hook). HAPI sends the shared secret in the
    # Authorization header of every delivery; the API compares it in constant
    # time. Required like the JWT key: with no secret anyone on the network
    # could forge a "new lab result" for any patient.
    fhir_hook_secret: str = Field(..., min_length=16)
    # URL HAPI calls. Containers reach each other by service name on the
    # internal Compose network, never through the public tunnel.
    fhir_hook_endpoint: str = "http://api:8000/api/v1/fhir/hooks"

    # PACS (Orthanc). Solo la API lo conoce: el navegador nunca le habla, de
    # modo que todo acceso a una imagen pasa antes por el token y por el rol,
    # y queda registrado en la bitacora de auditoria.
    orthanc_url: str = "http://localhost:8042"
    orthanc_user: str = "api"
    # Sin valor por defecto: si falta, el PACS se reporta como no configurado
    # en lugar de arrancar con una clave conocida escrita en el codigo.
    orthanc_password: str | None = None
    orthanc_timeout_seconds: int = 15

    # CORS. Vacio por defecto: la UI se sirve desde el mismo origen que la API
    # (nginx), asi que no hace falta. Lista separada por comas de origenes
    # exactos que si pueden llamar a la API desde otro sitio.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Evidencia de limpieza de camas. Con REQUIRE_CLEANING_EVIDENCE=true, una
    # cama solo pasa de CLEANING a AVAILABLE si tiene un registro de limpieza
    # APROBADO posterior a su ingreso a limpieza (409 en caso contrario). Con
    # false (por defecto) el cambio de estado se comporta como siempre.
    require_cleaning_evidence: bool = False

    # Billing (R09). Invoices live in MongoDB, apart from the clinical record.
    # No default URL: with MONGO_URL unset the API starts anyway and every
    # billing route answers 503, instead of guessing a host or a credential.
    mongo_url: str | None = None
    mongo_db: str = "saludred_contabilidad"
    mongo_timeout_ms: int = 2000

    # Critical alerts (R20). An in-process timer escalates an alert nobody has
    # acknowledged and raises the "free bed, not cleaned" alert. It is valid
    # because the API runs ONE worker (see docs/EVENTOS.md); tests turn it off.
    alert_escalation_enabled: bool = True
    # Minutes an alert may stay ``activa`` before admins and colleagues are told.
    # Lower it (for example to 1) to show the escalation in a demo.
    alert_escalation_minutes: int = Field(default=5, ge=0)
    # Seconds between ticks of the monitor.
    alert_monitor_interval_seconds: int = Field(default=30, ge=1)
    # Minutes a bed may sit in CLEANING without approved evidence before the
    # "free but not cleaned" alert is raised. Same default as /beds/cleaning/overdue.
    bed_cleaning_alert_minutes: int = Field(default=60, ge=0)

    # Logging: level of the ``app.*`` loggers.
    log_level: str = "INFO"

    # Seeding
    seed_default_password: str = "Demo2026!"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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
