from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+psycopg://village_insight:village_insight@localhost:5432/village_insight"
    )
    upload_root: Path = Path("data/uploads")
    secret_key_path: Path = Path("data/secrets/settings.key")
    source_path_manifest: Path | None = None
    import_roots: Annotated[list[Path], NoDecode] = [Path("data/import")]
    max_upload_bytes: int = 100 * 1024 * 1024
    max_batch_files: int = 500
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 120
    worker_min_available_memory_mb: int = 4096
    parse_workers: int = 2
    hermes_workers: int = 1
    materialize_workers: int = 1
    hermes_enabled: bool = False
    hermes_model: str | None = None
    hermes_fast_model: str | None = None
    hermes_reasoning_model: str | None = None
    hermes_provider: str | None = None
    hermes_base_url: str | None = None
    hermes_thinking_protocol: Literal["none", "deepseek"] = "none"
    hermes_api_key: str | None = None
    deepseek_api_key: str | None = None
    llm_multimodal_api_key: str | None = None
    hermes_max_iterations: int = 8
    # Leave unset to let Hermes/provider use the model's native output ceiling.
    # Configure only when an operator intentionally wants a smaller per-response cap.
    hermes_max_tokens: int | None = None
    hermes_thinking_enabled: bool = False
    hermes_reasoning_effort: str = "high"
    hermes_timeout_seconds: int = 120
    # Upper bound for the complete multi-stage ingestion recognition task. Each
    # provider call keeps its narrower timeout; this prevents their sum, fallbacks,
    # and reviews from holding a worker lease indefinitely.
    hermes_recognition_timeout_seconds: Annotated[int, Field(gt=0)] = 900
    hermes_enabled_toolsets: Annotated[list[str], NoDecode] = []
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    session_cookie_name: str = "vi_session"
    session_lifetime_hours: int = 12
    session_cookie_secure: bool = False
    bootstrap_tenant_name: str | None = None
    bootstrap_township_name: str | None = None
    bootstrap_village_name: str | None = None
    bootstrap_password: str | None = None

    @field_validator(
        "import_roots",
        "cors_origins",
        "hermes_enabled_toolsets",
        mode="before",
    )
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    def resolved_upload_root(self) -> Path:
        return self.upload_root.expanduser().resolve()

    def resolved_secret_key_path(self) -> Path:
        return self.secret_key_path.expanduser().resolve()

    def resolved_source_path_manifest(self) -> Path | None:
        if self.source_path_manifest is None:
            return None
        return self.source_path_manifest.expanduser().resolve()

    def resolved_import_roots(self) -> tuple[Path, ...]:
        return tuple(root.expanduser().resolve() for root in self.import_roots)


@lru_cache
def get_settings() -> Settings:
    return Settings()
