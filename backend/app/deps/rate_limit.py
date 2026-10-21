"""Límites de peticiones de la API (C-5, C-3).

Usa la librería ``limits`` (la misma que hay debajo de slowapi) con almacenamiento en memoria:
vale para un único proceso de uvicorn, que es como se arranca la API. Cada app de FastAPI tiene
su propio limitador en ``app.state.limitador`` (los tests crean apps nuevas y no se pisan).

* Login: ``rate_limit_login`` por IP.
* POST /refresh* y POST de featured-picks: ``rate_limit_refresh`` por usuario autenticado.
* Resto de la API: ``rate_limit_general`` por IP (middleware en ``seguridad_http.py``).
"""

from __future__ import annotations

import math
import time

from fastapi import Depends, HTTPException, Request, status
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from backend.app.config import get_settings
from backend.app.deps.auth_user import get_current_user
from backend.models import AppUser

MENSAJE_429 = "Demasiadas peticiones. Espera {segundos} s y vuelve a intentarlo."


class LimitadorApi:
    """Ventana deslizante en memoria; ``consumir`` devuelve los segundos de espera (0 = vale)."""

    def __init__(self, *, habilitado: bool = True) -> None:
        self.habilitado = habilitado
        self._estrategia = MovingWindowRateLimiter(MemoryStorage())
        self._cache: dict[str, RateLimitItem] = {}

    def _item(self, limite: str) -> RateLimitItem:
        item = self._cache.get(limite)
        if item is None:
            item = parse(limite)
            self._cache[limite] = item
        return item

    def consumir(self, limite: str, *claves: str) -> int:
        """Apunta una petición; si se pasa del límite devuelve cuántos segundos esperar."""
        if not self.habilitado:
            return 0
        item = self._item(limite)
        if self._estrategia.hit(item, *claves):
            return 0
        stats = self._estrategia.get_window_stats(item, *claves)
        return max(1, math.ceil(stats.reset_time - time.time()))


def error_429(segundos: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=MENSAJE_429.format(segundos=segundos),
        headers={"Retry-After": str(segundos)},
    )


def ip_cliente(request: Request) -> str:
    """IP de la conexión. No se fía de X-Forwarded-For (la API no está tras un proxy propio)."""
    return request.client.host if request.client else "desconocida"


def obtener_limitador(request: Request) -> LimitadorApi:
    limitador = getattr(request.app.state, "limitador", None)
    if limitador is None:
        limitador = LimitadorApi(habilitado=get_settings().rate_limit_enabled)
        request.app.state.limitador = limitador
    return limitador


def limite_login(request: Request) -> None:
    """Máximo ``rate_limit_login`` intentos de login por IP (aciertos incluidos)."""
    espera = obtener_limitador(request).consumir(
        get_settings().rate_limit_login, "login", ip_cliente(request)
    )
    if espera:
        raise error_429(espera)


def _limite_por_usuario(request: Request, alcance: str, user: AppUser) -> None:
    espera = obtener_limitador(request).consumir(
        get_settings().rate_limit_refresh, alcance, f"usuario:{user.id}"
    )
    if espera:
        raise error_429(espera)


def limite_refresh(request: Request, user: AppUser = Depends(get_current_user)) -> None:
    """Todos los POST /refresh* comparten cupo: lanzan scrapers y pueden bloquear la IP."""
    if request.method == "POST":
        _limite_por_usuario(request, "refresh", user)


def limite_featured_picks(request: Request, user: AppUser = Depends(get_current_user)) -> None:
    """POST de featured-picks (recalificar / asegurar fecha): cupo propio, mismo ritmo."""
    _limite_por_usuario(request, "featured-picks", user)
