"""Aplicación FastAPI de Bets AI.

Endpoints disponibles
---------------------

* ``GET /``                           — health check
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

Documentación interactiva auto-generada en:
* ``/docs``    (Swagger UI)
* ``/redoc``   (ReDoc)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError

from backend.app.api import analysis, auth, calibration, dashboard, featured_daily_picks, matches, me_parlays, players, profiles, refresh, statistics, value_bets

app = FastAPI(
    title="Bets AI",
    description=(
        "API para detectar apuestas con valor (value bets) en fútbol europeo. "
        "Combina cuotas de múltiples bookmakers con la estimación de probabilidad "
        "real del bookie afilado para encontrar oportunidades con EV positivo."
    ),
    version="0.1.0",
    contact={"name": "Bets AI"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(featured_daily_picks.router)
app.include_router(me_parlays.router)
app.include_router(matches.router)
app.include_router(value_bets.router)
app.include_router(profiles.router)
app.include_router(refresh.router)
app.include_router(calibration.router)
app.include_router(players.router)
app.include_router(statistics.router)


@app.exception_handler(ProgrammingError)
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


@app.exception_handler(OperationalError)
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


@app.get("/", tags=["health"])
def root() -> dict:
    """Health check sencillo."""
    return {
        "service": "Bets AI",
        "status": "ok",
        "version": app.version,
        "now": datetime.now(timezone.utc).isoformat(),
        "docs": "/docs",
    }
