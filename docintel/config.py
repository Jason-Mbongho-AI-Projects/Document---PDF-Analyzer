"""
Platform configuration.

Everything is environment-driven with development-safe defaults, so the stack
runs locally with no Postgres, Redis or Docker installed while still being
configured for them in production. The database URL and the storage/queue
driver names are the only things that need to change between the two.
"""
import secrets
from functools import lru_cache
from pathlib import Path
from typing import List, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_prefix="DOCINTEL_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"

    # --- database -------------------------------------------------------
    # SQLite by default so a clone runs immediately; point at Postgres in
    # production, e.g. postgresql+psycopg://user:pass@host/docintel
    database_url: str = f"sqlite:///{(ROOT / 'docintel.db').as_posix()}"

    # --- auth -----------------------------------------------------------
    secret_key: str = ""
    access_token_ttl_minutes: int = 60
    bcrypt_rounds: int = 12

    # "required" — every request must carry a valid bearer token.
    # "open"     — DEVELOPMENT ONLY. Requests with no token are treated as the
    #              built-in dev user so the app can be demonstrated without
    #              signing in. Authorization is NOT disabled: the dev user is a
    #              real user with a real workspace, so object-level checks and
    #              tenant isolation still apply exactly as in production.
    #
    # Defaults to "required" so a fresh deployment is closed. Opening it up has
    # to be a deliberate act in the environment, and is refused outright in
    # production (see the validator below).
    auth_mode: Literal["required", "open"] = "required"
    # .invalid is reserved by RFC 2606 and can never resolve, so this account
    # cannot receive mail or be confused with a real user.
    dev_user_email: str = "dev@docintel.invalid"

    # --- storage --------------------------------------------------------
    storage_driver: Literal["local", "s3"] = "local"
    storage_root: Path = ROOT / ".storage"
    s3_bucket: str = ""
    s3_region: str = ""

    # --- queue ----------------------------------------------------------
    queue_driver: Literal["database", "rq"] = "database"
    redis_url: str = ""
    worker_poll_seconds: float = 1.0
    job_max_attempts: int = 3
    job_timeout_seconds: int = 900

    # --- uploads --------------------------------------------------------
    max_upload_mb: int = 200
    allowed_mime_types: List[str] = ["application/pdf"]

    # --- ai ---------------------------------------------------------------
    # Accepts either DOCINTEL_OPENROUTER_API_KEY or the bare OPENROUTER_API_KEY
    # the original app already used, so one .env serves both.
    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DOCINTEL_OPENROUTER_API_KEY", "OPENROUTER_API_KEY",
        ),
    )

    # How many document sections to summarise concurrently.
    ai_workers: int = 4

    # --- feature flags ----------------------------------------------------
    feature_ocr: bool = False
    feature_rag: bool = False
    feature_conversion: bool = False
    feature_redaction: bool = False
    feature_translation: bool = False

    @field_validator("secret_key", mode="after")
    @classmethod
    def _require_secret_in_production(cls, value: str, info) -> str:
        env = info.data.get("environment", "development")
        if value:
            return value
        if env == "production":
            raise ValueError(
                "DOCINTEL_SECRET_KEY must be set in production. Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        # Ephemeral key for dev/test: tokens simply do not survive a restart,
        # which is preferable to shipping a hardcoded default that could reach
        # production by accident.
        return secrets.token_urlsafe(48)

    @field_validator("auth_mode", mode="after")
    @classmethod
    def _never_open_in_production(cls, value: str, info) -> str:
        """Hard stop: open access must never reach production.

        This is a refusal to boot rather than a warning, because a warning in a
        log is exactly the thing nobody reads before a deploy.
        """
        if value == "open" and info.data.get("environment") == "production":
            raise ValueError(
                "DOCINTEL_AUTH_MODE=open is not permitted when "
                "DOCINTEL_ENVIRONMENT=production. Open access disables "
                "authentication and would expose every document to anyone who "
                "can reach the service. Set DOCINTEL_AUTH_MODE=required."
            )
        return value

    @property
    def auth_open(self) -> bool:
        return self.auth_mode == "open"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
