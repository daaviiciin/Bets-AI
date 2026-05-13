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

    odds_api_key: str = Field(
        default="",
        description="The Odds API — vacío desactiva el refresco estándar de cuotas.",
    )
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

    # Pipeline de datos principal: estadísticas tipo Flashscore + Winamax cuotas,
    # sin depender de múltiples bookies para el primer criterio de EV.
    official_data_pipeline: bool = Field(default=False)
    primary_bookmaker_key: str = Field(default="winamax_es")
    odds_api_refresh_enabled: bool = Field(
        default=False,
        description="Si False, POST /refresh no llama a The Odds API (cero créditos).",
    )

    #: Tras ingestar Winamax: cuántos snapshots recientes conservar por liga;
    #: el resto se borran (CASCADE borra odds). Evita crecimiento ilimitado.
    winamax_snapshot_retention: int = Field(
        default=2,
        ge=1,
        le=50,
        description="Snapshots Winamax por liga a mantener; los más antiguos se purgan.",
    )

    # Filtrado y modelo de value bets (fútbol)
    value_bets_use_recent_goal_form: bool = Field(
        default=True,
        description="Mezcla media temporada con últimos partidos casa/fuera para λ Poisson.",
    )
    value_bets_recent_form_matches: int = Field(
        default=8, ge=3, le=15,
        description="Cuántos partidos recientes (como local o visitante) mezclar.",
    )
    value_bets_recent_form_weight: float = Field(
        default=0.38, ge=0.0, le=1.0,
        description="Peso del reciente sobre la temporada completa (0=sólo temporada, 1=sólo reciente).",
    )
    value_bets_h2h_min_model_probability: float = Field(
        default=0.22, ge=0.0, le=1.0,
        description="Mín. P modelo para publicar picks 1X2 (elimina outsider sólo por margen alto).",
    )

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    #: Firma para JWT de sesión MVP (sobrescribir en producción).
    jwt_secret: str = Field(
        default="betsai_dev_jwt_change_me",
        description="Secreto H256 para firmar tokens de /auth/login.",
    )
    jwt_access_ttl_days: int = Field(default=30, ge=1, le=365)

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
