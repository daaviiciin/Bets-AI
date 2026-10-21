"""Aplicación FastAPI de Bets AI.

Endpoints disponibles
---------------------

* ``GET /``                           — health check (público)
* ``POST /auth/login``                — login (público, con límite de intentos por IP)
* ``GET /matches``                    — lista de partidos próximos
* ``GET /matches/{id}``               — detalle de un partido
* ``GET /matches/{id}/odds``          — cuotas capturadas para un partido
* ``GET /value-bets``                 — value bets detectadas (con EV+)
* ``GET /dashboard/graded-odds``       — todas las cuotas Winamax recientes calificadas
* ``GET /profiles``                   — lista de perfiles de apostador
* ``GET /profiles/{name}``            — detalle de un perfil
* ``GET /profiles/{name}/recommendations`` — recomendaciones personalizadas
* ``GET /players``                    — búsqueda de jugadores por nombre
* ``GET /players/{id}``               — datos básicos de un jugador
* ``GET /players/{id}/stats``         — stats de temporada + histórico por partido

Todo salvo ``/`` y ``/auth/login`` exige ``Authorization: Bearer <token>``: la dependencia
``get_current_user`` se añade al registrar cada router, sin tocar su lógica. Los ``POST
/refresh*`` y los POST de featured-picks llevan además su propio límite por usuario.

Documentación interactiva (solo con ``ENVIRONMENT=development``):
* ``/docs``    (Swagger UI)
* ``/redoc``   (ReDoc)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError

from backend.app.api import (
    analysis,
    auth,
    calibration,
    dashboard,
    featured_daily_picks,
    matches,
    me_parlays,
    players,
    profiles,
    refresh,
    value_bets,
)
from backend.app.config import Settings, comprobar_config_api, get_settings
from backend.app.deps.auth_user import get_current_user
from backend.app.deps.rate_limit import LimitadorApi, limite_refresh
from backend.app.deps.seguridad_http import registrar_middlewares_seguridad

#: Routers que exigen sesión. ``auth`` va aparte: /auth/login es público y /auth/me ya pide token.
_ROUTERS_PROTEGIDOS = (
    analysis.router,
    dashboard.router,
    featured_daily_picks.router,
    me_parlays.router,
    matches.router,
    value_bets.router,
    profiles.router,
    calibration.router,
    players.router,
)


async def db_schema_mismatch_handler(request: Request, exc: ProgrammingError) -> JSONResponse:
    """Columnas/tablas ausentes (migración Alembic pendiente) → mensaje accionable."""
    _ = exc
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "La base de datos no coincide con los modelos (¿falta ejecutar migraciones?). "
                "Desde la raíz del proyecto: alembic upgrade head"
            ),
        },
    )


async def db_unavailable_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """PostgreSQL caído o credenciales/host incorrectos (típico: Docker no arrancado)."""
    _ = exc
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "No se pudo conectar a PostgreSQL. Arranca la base de datos "
                "(p. ej. `docker compose up -d`) y revisa DATABASE_URL / .env."
            ),
        },
    )


def crear_app(settings: Settings) -> FastAPI:
    """Construye la app. Valida antes la configuración de seguridad (falla si falta JWT_SECRET)."""
    comprobar_config_api(settings)
    docs = settings.docs_enabled
    app = FastAPI(
        title="Bets AI",
        description=(
            "API para detectar apuestas con valor (value bets) en fútbol europeo. "
            "Combina cuotas de múltiples bookmakers con la estimación de probabilidad "
            "real del bookie afilado para encontrar oportunidades con EV positivo."
        ),
        version="0.1.0",
        contact={"name": "Bets AI"},
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.limitador = LimitadorApi(habilitado=settings.rate_limit_enabled)

    # Orden: los middlewares añadidos después envuelven a los anteriores; CORS, el más externo.
    registrar_middlewares_seguridad(app, settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # la sesión va en la cabecera Authorization, no en cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    sesion = [Depends(get_current_user)]
    app.include_router(auth.router)
    for router in _ROUTERS_PROTEGIDOS:
        app.include_router(router, dependencies=sesion)
    app.include_router(refresh.router, dependencies=[*sesion, Depends(limite_refresh)])

    app.add_exception_handler(ProgrammingError, db_schema_mismatch_handler)
    app.add_exception_handler(OperationalError, db_unavailable_handler)

    @app.get("/", tags=["health"])
    def root() -> dict:
        """Health check sencillo (público)."""
        return {
            "service": "Bets AI",
            "status": "ok",
            "version": app.version,
            "now": datetime.now(timezone.utc).isoformat(),
            "docs": "/docs" if docs else None,
        }

    return app


app = crear_app(get_settings())
