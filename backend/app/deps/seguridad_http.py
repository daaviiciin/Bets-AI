"""Middlewares HTTP de seguridad: cabeceras, tamaño máximo de petición y límite general.

* Cabeceras: ``X-Content-Type-Options``, ``X-Frame-Options``, ``Referrer-Policy`` y una CSP
  mínima. La API solo devuelve JSON, así que su CSP es ``default-src 'none'``; Swagger/ReDoc
  (solo en desarrollo) cargan scripts de un CDN y se quedan sin CSP salvo ``frame-ancestors``.
  La CSP de la API no afecta al frontend de Angular (fotos de static.flashscore.com, escudos):
  esas páginas las sirve otro servidor.
* Tamaño: rechaza con 413 las peticiones cuyo ``Content-Length`` supera
  ``max_request_body_bytes``.
* Límite general por IP (``rate_limit_general``) para todo salvo los preflight CORS.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from backend.app.config import Settings
from backend.app.deps.rate_limit import MENSAJE_429, ip_cliente, obtener_limitador

_RUTAS_DOCS = ("/docs", "/redoc")

CABECERAS_BASE = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}
CSP_API = "default-src 'none'; frame-ancestors 'none'"
CSP_DOCS = "frame-ancestors 'none'"


def registrar_middlewares_seguridad(app: FastAPI, settings: Settings) -> None:
    """Añade los middlewares. Llamar antes de añadir CORS (que debe quedar el más externo)."""
    limite_general = settings.rate_limit_general
    max_bytes = settings.max_request_body_bytes

    @app.middleware("http")
    async def limitar_peticiones(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        longitud = request.headers.get("content-length")
        if longitud is not None:
            try:
                demasiado = int(longitud) > max_bytes
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Content-Length no válido."})
            if demasiado:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Petición demasiado grande (máximo {max_bytes} bytes)."},
                )
        if request.method != "OPTIONS":
            espera = obtener_limitador(request).consumir(
                limite_general, "general", ip_cliente(request)
            )
            if espera:
                return JSONResponse(
                    status_code=429,
                    content={"detail": MENSAJE_429.format(segundos=espera)},
                    headers={"Retry-After": str(espera)},
                )
        return await call_next(request)

    @app.middleware("http")
    async def cabeceras_seguridad(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for nombre, valor in CABECERAS_BASE.items():
            response.headers.setdefault(nombre, valor)
        es_docs = request.url.path.startswith(_RUTAS_DOCS)
        response.headers.setdefault("Content-Security-Policy", CSP_DOCS if es_docs else CSP_API)
        return response
