"""Aplicación FastAPI de Bets AI.

Endpoints disponibles
---------------------

* ``GET /``                           — health check
* ``GET /matches``                    — lista de partidos próximos
* ``GET /matches/{id}``               — detalle de un partido
* ``GET /matches/{id}/odds``          — cuotas capturadas para un partido
* ``GET /value-bets``                 — value bets detectadas (con EV+)
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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import calibration, matches, players, profiles, refresh, value_bets

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

app.include_router(matches.router)
app.include_router(value_bets.router)
app.include_router(profiles.router)
app.include_router(refresh.router)
app.include_router(calibration.router)
app.include_router(players.router)


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
