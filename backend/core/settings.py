"""Validated environment settings."""

from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    development = "development"
    test = "test"
    production = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BB_", extra="ignore")

    app_name: str = Field(default="FloCast", min_length=1)
    environment: Environment = Environment.development
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    log_level: str = "INFO"

    # LLM provider settings are server-only and optional: the rest of the app
    # starts and works even when no LLM is configured.
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=60.0, ge=1.0, allow_inf_nan=False)
    llm_access_token: SecretStr = SecretStr("")
    llm_allow_remote: bool = False
    llm_requests_per_minute: int = Field(default=30, ge=1, le=10000)
    llm_max_concurrent_requests: int = Field(default=2, ge=1, le=100)
    llm_max_tokens: int = Field(default=800, ge=1, le=16000)
    llm_max_answer_chars: int = Field(default=12000, ge=1, le=100000)
    llm_summary_cache_ttl_seconds: float = Field(default=60, ge=0, le=3600, allow_inf_nan=False)

    @field_validator("llm_base_url")
    @classmethod
    def validate_llm_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        url = urlsplit(value)
        # Accessing port also validates malformed/non-numeric port numbers.
        _ = url.port
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("BB_LLM_BASE_URL: нужна HTTP(S) база API без credentials, query и fragment")
        return value

    @field_validator("api_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value == "/" or value.endswith("/"):
            raise ValueError("API prefix must begin with / and have no trailing /")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, value: list[str]) -> list[str]:
        if not value or any(origin == "*" or not origin.startswith(("http://", "https://")) for origin in value):
            raise ValueError("CORS origins must be explicit HTTP(S) origins")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Unsupported log level")
        return normalized

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key.get_secret_value() and self.llm_model)
