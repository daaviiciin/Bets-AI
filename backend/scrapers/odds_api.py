"""Scraper de The Odds API.

Captura las cuotas de las 8 competiciones objetivo (5 grandes ligas + 3
competiciones europeas UEFA) en los mercados configurados y las guarda en
``data/raw/`` como JSON crudo, listo para ser procesado en fases posteriores.

Uso:

    python -m backend.scrapers.odds_api               # todas las ligas
    python -m backend.scrapers.odds_api --liga la_liga # solo una liga
    python -m backend.scrapers.odds_api --dry-run     # sin tocar disco
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import Settings, get_settings

LIGAS: dict[str, str] = {
    "la_liga": "soccer_spain_la_liga",
    "premier_league": "soccer_epl",
    "bundesliga": "soccer_germany_bundesliga",
    "serie_a": "soccer_italy_serie_a",
    "ligue_1": "soccer_france_ligue_one",
    "champions_league": "soccer_uefa_champs_league",
    "europa_league": "soccer_uefa_europa_league",
    "conference_league": "soccer_uefa_europa_conference_league",
}

DEFAULT_REGIONS = "eu"
# Solo "featured markets" del endpoint /odds (baratos, 1 crédito por liga).
# Para `double_chance`, `btts` o `draw_no_bet` hay que usar /events/{id}/odds
# (Fase 1.5), que cobra 1 crédito por partido.
DEFAULT_MARKETS = "h2h,totals"
DEFAULT_ODDS_FORMAT = "decimal"
REQUEST_TIMEOUT_SECONDS = 30


@dataclass
class FetchResult:
    """Resultado de capturar una sola liga."""

    liga: str
    sport_key: str
    n_eventos: int
    n_bookmakers: int
    file_path: Path | None
    requests_remaining: int | None
    requests_used: int | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _configurar_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _construir_url(settings: Settings, sport_key: str) -> str:
    return f"{settings.odds_api_base_url}/sports/{sport_key}/odds"


def _construir_params(
    api_key: str,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
    odds_format: str = DEFAULT_ODDS_FORMAT,
) -> dict[str, str]:
    return {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    }


def _contar_bookmakers_unicos(eventos: list[dict]) -> int:
    keys: set[str] = set()
    for evento in eventos:
        for bm in evento.get("bookmakers", []):
            if key := bm.get("key"):
                keys.add(key)
    return len(keys)


def _ruta_destino(raw_dir: Path, liga: str, timestamp: datetime) -> Path:
    fecha = timestamp.strftime("%Y%m%d_%H%M%S")
    return raw_dir / f"{liga}__{fecha}.json"


def fetch_liga(
    liga: str,
    sport_key: str,
    settings: Settings,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
    dry_run: bool = False,
) -> FetchResult:
    """Captura las cuotas de una liga concreta."""
    log = logging.getLogger(__name__)
    url = _construir_url(settings, sport_key)
    params = _construir_params(settings.odds_api_key, regions, markets)

    log.info("Pidiendo cuotas: liga=%s sport_key=%s", liga, sport_key)

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.error("Error de red para %s: %s", liga, exc)
        return FetchResult(
            liga=liga, sport_key=sport_key, n_eventos=0, n_bookmakers=0,
            file_path=None, requests_remaining=None, requests_used=None,
            error=f"network_error: {exc}",
        )

    remaining = response.headers.get("x-requests-remaining")
    used = response.headers.get("x-requests-used")

    if response.status_code != 200:
        log.error(
            "API respondió %s para %s: %s",
            response.status_code, liga, response.text[:200],
        )
        return FetchResult(
            liga=liga, sport_key=sport_key, n_eventos=0, n_bookmakers=0,
            file_path=None,
            requests_remaining=int(remaining) if remaining else None,
            requests_used=int(used) if used else None,
            error=f"http_{response.status_code}: {response.text[:200]}",
        )

    eventos = response.json()
    n_eventos = len(eventos)
    n_bookmakers = _contar_bookmakers_unicos(eventos)

    file_path: Path | None = None
    if not dry_run and n_eventos > 0:
        timestamp = datetime.now(timezone.utc)
        file_path = _ruta_destino(settings.raw_data_dir, liga, timestamp)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": {
                "liga": liga,
                "sport_key": sport_key,
                "regions": regions,
                "markets": markets,
                "captured_at": timestamp.isoformat(),
                "requests_remaining": int(remaining) if remaining else None,
                "requests_used": int(used) if used else None,
            },
            "events": eventos,
        }
        file_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    log.info(
        "%s -> %d eventos, %d bookies, créditos restantes: %s",
        liga, n_eventos, n_bookmakers, remaining,
    )

    return FetchResult(
        liga=liga, sport_key=sport_key, n_eventos=n_eventos,
        n_bookmakers=n_bookmakers, file_path=file_path,
        requests_remaining=int(remaining) if remaining else None,
        requests_used=int(used) if used else None,
    )


def fetch_todas(
    settings: Settings | None = None,
    ligas: dict[str, str] | None = None,
    regions: str = DEFAULT_REGIONS,
    markets: str = DEFAULT_MARKETS,
    dry_run: bool = False,
) -> list[FetchResult]:
    """Captura todas las ligas configuradas y devuelve los resultados."""
    settings = settings or get_settings()
    ligas = ligas or LIGAS

    resultados: list[FetchResult] = []
    for liga, sport_key in ligas.items():
        resultado = fetch_liga(
            liga=liga, sport_key=sport_key, settings=settings,
            regions=regions, markets=markets, dry_run=dry_run,
        )
        resultados.append(resultado)
    return resultados


def imprimir_resumen(resultados: list[FetchResult]) -> None:
    log = logging.getLogger(__name__)

    total_eventos = sum(r.n_eventos for r in resultados)
    n_ok = sum(1 for r in resultados if r.ok)
    n_fail = len(resultados) - n_ok
    creditos_restantes = next(
        (r.requests_remaining for r in reversed(resultados)
         if r.requests_remaining is not None),
        None,
    )
    creditos_usados_total = next(
        (r.requests_used for r in reversed(resultados)
         if r.requests_used is not None),
        None,
    )

    log.info("=" * 70)
    log.info("RESUMEN DE CAPTURA")
    log.info("=" * 70)
    for r in resultados:
        estado = "OK " if r.ok else "ERR"
        log.info(
            "  [%s] %-20s %3d eventos, %2d bookies",
            estado, r.liga, r.n_eventos, r.n_bookmakers,
        )
        if not r.ok:
            log.info("       error: %s", r.error)
    log.info("-" * 70)
    log.info("Total eventos capturados : %d", total_eventos)
    log.info("Ligas OK / con error     : %d / %d", n_ok, n_fail)
    if creditos_restantes is not None:
        log.info("Créditos API restantes   : %d", creditos_restantes)
    if creditos_usados_total is not None:
        log.info("Créditos API usados      : %d (total acumulado del mes)",
                 creditos_usados_total)
    log.info("=" * 70)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper de The Odds API.")
    parser.add_argument(
        "--liga", choices=list(LIGAS.keys()), default=None,
        help="Captura solo una liga concreta (por defecto: todas).",
    )
    parser.add_argument(
        "--regions", default=DEFAULT_REGIONS,
        help=f"Regiones a consultar (default: {DEFAULT_REGIONS})",
    )
    parser.add_argument(
        "--markets", default=DEFAULT_MARKETS,
        help=f"Mercados a consultar (default: {DEFAULT_MARKETS})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Lanza las peticiones pero no guarda nada en disco.",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)

    ligas_a_procesar = (
        {args.liga: LIGAS[args.liga]} if args.liga else LIGAS
    )

    resultados = fetch_todas(
        settings=settings, ligas=ligas_a_procesar,
        regions=args.regions, markets=args.markets, dry_run=args.dry_run,
    )

    imprimir_resumen(resultados)

    return 0 if all(r.ok for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
