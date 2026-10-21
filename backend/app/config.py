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
    #: Sin valor por defecto a propósito (C-12): si falta y no hay DATABASE_URL, error claro.
    postgres_password: str = Field(default="")
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
        default=12,
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

    #: Secreto HS256 de los JWT de sesión. Sin valor por defecto (C-1): la API no arranca sin él
    #: (ver ``comprobar_config_api``). Generar con
    #: ``python -c "import secrets; print(secrets.token_urlsafe(48))"`` y ponerlo en .env.
    jwt_secret: str = Field(
        default="",
        description="Secreto HS256 para firmar tokens de /auth/login (JWT_SECRET en .env).",
    )
    jwt_access_ttl_days: int = Field(default=7, ge=1, le=30)

    # Seguridad HTTP de la API
    #: Orígenes permitidos por CORS, separados por comas (C-10). El frontend usa el proxy de
    #: Angular (mismo origen), así que esto solo afecta a llamadas directas a :8000.
    cors_allowed_origins: str = Field(default="http://localhost:4200,http://127.0.0.1:4200")
    #: Límites de peticiones (sintaxis de la librería ``limits``: "5/minute", "1 per 3 minutes").
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_login: str = Field(default="5/minute", description="Intentos de login por IP.")
    rate_limit_refresh: str = Field(
        default="1 per 3 minutes",
        description="POST /refresh* (y POST de featured-picks) por usuario.",
    )
    rate_limit_general: str = Field(default="120/minute", description="Resto de la API, por IP.")
    #: Tamaño máximo del cuerpo de una petición (cabecera Content-Length), en bytes.
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        """``/docs``, ``/redoc`` y ``/openapi.json`` solo en desarrollo (C-11)."""
        return self.environment.strip().lower() == "development"

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
        if not self.postgres_password:
            raise RuntimeError(
                "Falta la contraseña de PostgreSQL: define POSTGRES_PASSWORD (o DATABASE_URL) "
                "en el .env de la raíz del proyecto."
            )
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


#: Longitud mínima del secreto JWT (``token_urlsafe(48)`` da 64 caracteres).
JWT_SECRET_MIN_LENGTH = 32

#: Valor por defecto que tuvo el código hasta 2026-10-19: público en el repo, nunca válido.
_JWT_SECRET_ANTIGUO = "betsai_dev_jwt_change_me"


def comprobar_config_api(settings: Settings) -> None:
    """Falla al arrancar la API si la configuración de seguridad no es válida."""
    _ = settings.resolved_database_url  # sin contraseña de PostgreSQL → RuntimeError claro
    secreto = settings.jwt_secret.strip()
    if not secreto:
        raise RuntimeError(
            "Falta JWT_SECRET en .env. Genera uno con "
            '`python -c "import secrets; print(secrets.token_urlsafe(48))"` y añade '
            "la línea JWT_SECRET=<valor> al .env de la raíz."
        )
    if len(secreto) < JWT_SECRET_MIN_LENGTH or secreto == _JWT_SECRET_ANTIGUO:
        raise RuntimeError(
            f"JWT_SECRET es demasiado corto o inseguro (mínimo {JWT_SECRET_MIN_LENGTH} "
            "caracteres aleatorios). Genera uno con secrets.token_urlsafe(48)."
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve una única instancia de Settings (patrón singleton)."""
    return Settings()
