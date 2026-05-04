"""Carga centralizada de configuración desde variables de entorno (.env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Configuración del proyecto leída del fichero .env de la raíz."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    odds_api_key: str = Field(..., description="API key de The Odds API")
    odds_api_base_url: str = Field(default="https://api.the-odds-api.com/v4")

    postgres_user: str = Field(default="betsai")
    postgres_password: str = Field(default="changeme")
    postgres_db: str = Field(default="betsai")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    database_url: str | None = Field(default=None)

    llm_provider: str = Field(default="gemini")
    gemini_api_key: str | None = Field(default=None)
    groq_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def raw_data_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_data_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve una única instancia de Settings (patrón singleton)."""
    return Settings()
