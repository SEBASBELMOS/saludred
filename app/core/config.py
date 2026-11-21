"""Runtime configuration, loaded from environment variables.

No credential is ever hardcoded: every secret is read from the environment, and
``.env`` is excluded from version control. ``.env.example`` documents the shape
of the configuration without carrying any real value.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator, model_validator
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
    # The algorithm is NOT configurable: it is fixed to HS256 in app/core/security.py.
    # Short-lived on purpose (minutes): a stolen token is useful only briefly. The
    # frontend has no refresh flow, so a session ends when the token does.
    access_token_expire_minutes: int = Field(default=15, ge=1)
    # ``iss`` / ``aud`` of the user token. Any other token (another issuer, the
    # system tokens of the interoperability flows) is rejected on user routes.
    jwt_issuer: str = "saludred"
    jwt_audience: str = "saludred-api"

    # Patient field encryption (AES-256-GCM, app/core/field_crypto.py). Both unset:
    # nothing is encrypted and the application behaves exactly as before.
    # Format: ``k1:<base64 of 32 bytes>,k2:<...>``. New values are written with the
    # ACTIVE key; any listed key still decrypts, which is how a key is rotated.
    field_encryption_keys: str | None = None
    field_encryption_active_kid: str | None = None
    # HMAC key of the blind index of the document (searchable without decrypting).
    # MUST differ from every encryption key. Unset: lookups compare the legacy plain
    # text, as before.
    blind_index_key: str | None = None

    # Interoperability between teams (docs/INTEROP.md).
    # Secret of the federated patient id (HMAC-SHA256 of the normalized document). It is
    # agreed by every team of the exchange (the professor hands it out, slide 33), so it
    # is a SECRET read from the environment, never committed. Unset: the federated id
    # is disabled -- the receiver answers 503 and the sender refuses to send.
    federated_secret: str | None = None
    # URI of the shared identifier system (``subject.identifier.system``). The sources
    # do not give it (slide 27 only shows ``SID_FEDERADO``): this is a PLACEHOLDER that
    # must be replaced by the value the professor announces.
    federated_system: str = "urn:uao:salud-digital:paciente-federado"
    # Secret that signs the system tokens. Unset: the user JWT secret is reused; the
    # tokens stay apart anyway because their ``aud`` differs (saludred-interop).
    interop_jwt_secret: str | None = None
    interop_audience: str = "saludred-interop"
    interop_token_ttl_seconds: int = Field(default=600, ge=30, le=3600)
    # A signed message whose date is further than this from the clock is refused (401).
    interop_signature_tolerance_seconds: int = Field(default=300, ge=1, le=3600)
    # Critical glucose (mg/dL), POR CONFIRMAR: the slides only show 310 as critical and
    # 182 as normal. >= high or <= low raises the critical alert.
    interop_glucose_critical_high: float = 300.0
    interop_glucose_critical_low: float = 54.0
    # Specialist (user id or username) who receives an incoming referral when the
    # target IPS has no active especialista. Unset: the referral waits in the queue.
    interop_default_specialist: str | None = None
    # Any-partner mode (docs/INTEROP.md, "Contrato para cualquier equipo"). Every default
    # keeps the strict behaviour; each switch relaxes ONE control by agreement with a partner.
    # Accept ``X-Api-Key: <client_id>:<client_secret>`` instead of the OAuth Bearer token on
    # the /interop/* entry routes only (never on /api/v1).
    interop_allow_api_key: bool = False
    # Require the HMAC signature (date, nonce, X-Interop-Firma). False: a message WITHOUT
    # signature is accepted and the nonce and date checks are skipped; one that carries a
    # signature is still verified.
    interop_require_signature: bool = True
    # Accept a Patient identified by a national document (type and number) instead of the
    # federated id; the receiver computes the federated id itself. A negotiated trade-off
    # against data minimization (Ley 1581 de 2012).
    interop_accept_document_identifier: bool = False
    # Also send the document identifier of the patient in the Bundle we build (the federated
    # id is always sent). For partners that cannot compute the federated id.
    interop_send_document_identifier: bool = False
    # System of the document identifier (type and number) of the exchange.
    interop_document_system: str = "urn:uao:salud-digital:documento"

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
    # Embeddings are computed by the same service. The first call loads the
    # model into memory, so it gets a much longer timeout than a prediction.
    ml_embed_timeout_seconds: float = 30.0

    # Clinical RAG (docs/RAG.md). The language model is optional: without a key
    # the answer is extractive (the best passages, quoted). Never hardcode a key.
    groq_api_key: str | None = None
    groq_model: str = "qwen/qwen3.8-27b"
    # Model that grades the evaluation. Unset: the generator model. Groq limits are
    # per model, so a different one splits the quota (and avoids self-grading).
    rag_judge_model: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 30.0
    # Live answers: a stalled request is abandoned early and sent again.
    groq_attempt_timeout_seconds: float = Field(default=8.0, gt=0)
    groq_timeout_retries: int = Field(default=2, ge=0, le=5)
    # Caps the completion. qwen3 "thinks" before answering; without a cap a long
    # reasoning can exhaust the time and the free-tier token budget.
    groq_max_tokens: int = Field(default=600, ge=50, le=4000)
    # Abstain only when BOTH signals are weak: best cosine below the first AND
    # best BM25 score below the second (calibrated on the professor's notebook).
    rag_min_cosine: float = 0.45
    rag_min_bm25: float = 1.5
    # Dense candidates below this cosine are not fused: a nearest neighbour that
    # is not close is noise, and RRF would still rank it.
    # Weighted RRF: fused = w_dense / (60 + rank_dense) + w_lexical / (60 + rank_bm25).
    # 1.0 / 1.0 is plain RRF. BM25 is weak on a mixed es/en corpus, so the dense
    # side can be given more weight (calibrate with ``rag_eval --sweep``).
    rag_rrf_dense_weight: float = Field(default=3.0, ge=0.0, le=10.0)
    rag_rrf_lexical_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    rag_dense_floor: float = Field(default=0.25, ge=0.0, le=1.0)
    # MMR trade-off between relevance (1.0) and diversity (0.0).
    rag_mmr_lambda: float = Field(default=0.7, ge=0.0, le=1.0)
    # Hybrid candidates handed to MMR, and chunks finally cited in the prompt.
    rag_candidates: int = Field(default=8, ge=1, le=50)
    rag_default_k: int = Field(default=5, ge=1, le=10)

    # Clinical agent (docs/AGENTE.md). It uses the same Groq account and model as
    # the RAG (GROQ_*); these only bound one agent turn. Without GROQ_API_KEY the
    # agent degrades to the extractive guideline search, it never fails.
    agent_max_tokens: int = Field(default=600, ge=50, le=4000)
    # Wall-clock limit of one turn (several model calls plus the tools).
    agent_timeout_seconds: float = Field(default=90.0, gt=0)
    # Retries of the Groq SDK on 429 / transient errors (it honours retry-after).
    agent_model_retries: int = Field(default=4, ge=0, le=8)
    # Model calls in flight at the same time, across all conversations.
    agent_max_concurrent_calls: int = Field(default=4, ge=1, le=32)
    # "postgres": checkpointer and store in the application database (production).
    # "memory": in-process, lost on restart (tests, or if postgres cannot start).
    agent_memory: str = Field(default="postgres", pattern="^(postgres|memory)$")
    # Test mode: the clinician's answer then also carries ``ruta``/``motivo`` and the
    # harness logs the detail of each turn. In normal mode only errors and
    # security events (logger ``app.security``) are written.
    agent_debug: bool = False
    # Turns (chat + decide) running at the same time in this process. Beyond it the
    # endpoint answers 429 instead of queueing without bound.
    agent_max_inflight_turns: int = Field(default=8, ge=1, le=64)
    # Token window of the working memory (messages sent to the model), approximate.
    agent_context_max_tokens: int = Field(default=16000, ge=1000, le=32000)
    # Reasoning effort sent to the provider (e.g. "low", "none"). Unset: the model
    # default (``low`` for gpt-oss, hidden reasoning for the rest). Check the value
    # against the configured model before setting it: an unknown one is a 400.
    agent_reasoning_effort: str | None = None
    # Temporary IP block on /agente/* after repeated injection or off-topic abuse.
    # Strikes are counted inside the block window; 0 minutes disables the block.
    agent_ip_block_minutes: int = Field(default=15, ge=0, le=1440)
    agent_ip_block_strikes: int = Field(default=3, ge=1, le=50)
    # Trust the CF-Connecting-IP header for the client address. Only safe when the
    # API is reachable ONLY through Cloudflare; otherwise anyone can forge it.
    trust_cf_connecting_ip: bool = False

    # Rate limit per client IP (sliding window, app/core/ratelimit.py). The strict
    # cap applies to login, /oauth/token, /interop/* and /agente/*; every other
    # route (the SPA makes many calls per screen) gets the general cap. /health
    # and the live event channels are never limited.
    rate_limit_enabled: bool = True
    rate_limit_max_requests: int = Field(default=10, ge=1)
    rate_limit_window_seconds: float = Field(default=10.0, gt=0)
    rate_limit_general_max_requests: int = Field(default=300, ge=1)

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

    # Automatic AI analysis. When the core vital signs of an open encounter are
    # all recorded, the criticality analysis runs on its own (event reactor plus
    # background work); nobody presses a button. Tests force it off.
    ai_auto_analysis: bool = True
    # Fallback of the monitor tick: an open encounter whose usable variables have
    # not changed for this long (and meet the minimum) is analyzed with what it has.
    ai_auto_grace_seconds: int = Field(default=120, ge=0)
    # The fallback only looks at recent activity, so switching the feature on
    # never back-fills the old encounters of the dataset.
    ai_auto_lookback_minutes: int = Field(default=60, ge=1)

    # Clinical judgment on sign-off. With REQUIRE_REVIEW_NOTE=true approving (or
    # rejecting) a report without a written concept of at least 10 characters
    # answers 422. With false (default) the API stays tolerant: R10 says the
    # clinician "puede agregar observaciones", and the automatic tester approves
    # without text. The web UI always asks for it.
    require_review_note: bool = False

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

    @model_validator(mode="after")
    def _check_field_crypto(self) -> "Settings":
        """Fail at start-up, not at the first patient write, on a bad key setup."""

        # Imported here: field_crypto reads the settings, so a top-level import loops.
        from app.core.field_crypto import validate_key_material

        validate_key_material(
            self.field_encryption_keys,
            self.field_encryption_active_kid,
            self.blind_index_key,
        )
        return self

    @property
    def federated_secret_value(self) -> str | None:
        return (self.federated_secret or "").strip() or None

    @property
    def interop_signing_key(self) -> str:
        return (self.interop_jwt_secret or "").strip() or self.jwt_secret_key

    @property
    def has_llm_key(self) -> bool:
        return bool((self.groq_api_key or "").strip())

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
