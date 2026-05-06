"""Scraper de la API no-oficial de Sofascore.

Funciona con ``curl-cffi`` que mimetiza el TLS handshake de Chrome para evitar
bloqueos de bot detection. La API no requiere autenticación pero sí espera
cabeceras de navegador real.

Endpoints utilizados
--------------------

* ``/unique-tournament/{id}/seasons``
  Lista de temporadas. La primera es la actual.

* ``/unique-tournament/{id}/season/{sid}/top-players/overall``
  Top 100 jugadores por cada una de ~29 estadísticas (goals, shotsOnTarget,
  expectedGoals, totalShots, ...). Una sola llamada nos devuelve ~1.8 MB
  de datos cubriendo todos los atacantes y centrocampistas relevantes.

* ``/unique-tournament/{id}/season/{sid}/standings/total``
  Clasificación liga regular con totales (puntos, GF, GA, etc.).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests as cf

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import Settings, get_settings

BASE_URL = "https://api.sofascore.com/api/v1"
REQUEST_TIMEOUT = 30
RATE_LIMIT_SLEEP_SECONDS = 1.5

# Mapeo sport_key (Odds API) → (nombre legible, tournament_id Sofascore)
TOURNAMENTS: dict[str, tuple[str, int]] = {
    "soccer_spain_la_liga": ("La Liga", 8),
    "soccer_epl": ("Premier League", 17),
    "soccer_germany_bundesliga": ("Bundesliga", 35),
    "soccer_italy_serie_a": ("Serie A", 23),
    "soccer_france_ligue_one": ("Ligue 1", 34),
}


@dataclass
class LeagueFetchResult:
    """Resultado de capturar todas las páginas de una liga."""

    sport_key: str
    league_name: str
    tournament_id: int
    season_id: int | None = None
    season_name: str | None = None
    top_players_path: Path | None = None
    standings_path: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.top_players_path is not None


def _log() -> logging.Logger:
    return logging.getLogger(__name__)


def _get(url: str) -> dict:
    """GET con TLS de Chrome y cabeceras realistas."""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
    }
    r = cf.get(url, headers=headers, impersonate="chrome", timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} en {url}: {r.text[:200]}")
    return r.json()


def fetch_seasons(tournament_id: int) -> list[dict]:
    """Lista de temporadas (la primera = actual)."""
    return _get(f"{BASE_URL}/unique-tournament/{tournament_id}/seasons").get("seasons", [])


def fetch_top_players(tournament_id: int, season_id: int) -> dict:
    """Top jugadores por cada categoría (goals, shotsOnTarget, xG, etc.)."""
    return _get(
        f"{BASE_URL}/unique-tournament/{tournament_id}"
        f"/season/{season_id}/top-players/overall"
    )


def fetch_standings(tournament_id: int, season_id: int) -> dict:
    """Clasificación con totales de equipos."""
    return _get(
        f"{BASE_URL}/unique-tournament/{tournament_id}"
        f"/season/{season_id}/standings/total"
    )


def fetch_event_lineups(event_id: str | int) -> dict:
    """Lineups + estadísticas individuales de un partido finalizado.

    Estructura de la respuesta::

        {
          "home": {
            "players": [
              {
                "player": {"id": ..., "name": ..., "shortName": ..., ...},
                "shirtNumber": int,
                "position": "G/D/M/F",
                "substitute": bool,
                "statistics": {
                  "goals": ..., "totalShots": ..., "onTargetScoringAttempt": ...,
                  "rating": ..., "minutesPlayed": ..., ...
                }
              },
              ...
            ],
            "formation": str,
            "missingPlayers": [...]
          },
          "away": { ... }
        }
    """
    return _get(f"{BASE_URL}/event/{event_id}/lineups")


def fetch_event_incidents(event_id: str | int) -> dict:
    """Incidencias del partido (goles, tarjetas, sustituciones).

    Útil para extraer tarjetas amarillas/rojas por jugador, que no aparecen
    en ``/lineups``. Estructura::

        {"incidents": [{"incidentType": "card", "player": {...}, ...}, ...]}
    """
    return _get(f"{BASE_URL}/event/{event_id}/incidents")


# Mapeo de claves Sofascore → claves canónicas usadas por el modelo.
# Sólo incluimos las que de verdad aprovechamos en :class:`PlayerMatchStats`.
_PLAYER_STAT_MAP: dict[str, str] = {
    "goals": "goals",
    "goalAssist": "assists",
    "totalShots": "shots",
    "onTargetScoringAttempt": "shots_on_target",
    "shotOffTarget": "shots_off_target",
    "blockedScoringAttempt": "shots_blocked",
    "bigChanceCreated": "big_chances_created",
    "bigChanceMissed": "big_chances_missed",
    "expectedGoals": "expected_goals",
    "expectedAssists": "expected_assists",
    "minutesPlayed": "minutes_played",
    "touches": "touches",
    "totalPass": "passes_total",
    "accuratePass": "passes_accurate",
    "keyPass": "key_passes",
    "totalCross": "crosses_total",
    "accurateCross": "crosses_accurate",
    "totalLongBalls": "long_balls_total",
    "accurateLongBalls": "long_balls_accurate",
    "totalTackle": "tackles",
    "interceptionWon": "interceptions",
    "totalClearance": "clearances",
    "outfielderBlock": "blocks",
    "aerialWon": "aerial_duels_won",
    "wonContest": "dribbles_succeeded",
    "totalContest": "dribbles_attempted",
    "fouls": "fouls_committed",
    "wasFouled": "fouls_drawn",
    "totalOffside": "offsides",
    "saves": "saves",
    "goalsPrevented": "goals_prevented",
    "rating": "rating",
}

# Claves de stats que son numéricos enteros (los demás se tratan como Decimal).
_INT_STAT_KEYS = {
    "goals", "assists", "shots", "shots_on_target", "shots_off_target",
    "shots_blocked", "big_chances_created", "big_chances_missed",
    "minutes_played", "touches", "passes_total", "passes_accurate", "key_passes",
    "crosses_total", "crosses_accurate", "long_balls_total", "long_balls_accurate",
    "tackles", "interceptions", "clearances", "blocks",
    "aerial_duels_won", "dribbles_succeeded", "dribbles_attempted",
    "fouls_committed", "fouls_drawn", "offsides", "saves",
}


def parse_event_lineups(payload: dict) -> dict[str, list[dict]]:
    """Convierte el JSON de Sofascore en una estructura cómoda por equipo.

    Devuelve::

        {
          "home": [
            {
              "external_id": "1131909",
              "name": "Leo Román",
              "short_name": "L. Román",
              "position": "G",
              "is_starter": True,
              "is_substitute": False,
              "shirt_number": 1,
              "stats": {"goals": 0, "shots": 0, "rating": 7.5, ...},
            },
            ...
          ],
          "away": [...]
        }

    Sólo incluimos jugadores con ``minutesPlayed > 0``: los suplentes que no
    salieron del banquillo no aportan stats individuales.
    """
    out: dict[str, list[dict]] = {"home": [], "away": []}
    for side in ("home", "away"):
        block = payload.get(side) or {}
        for entry in block.get("players", []):
            player = entry.get("player") or {}
            ext_id = player.get("id")
            if ext_id is None:
                continue
            stats_raw = entry.get("statistics") or {}
            minutes = stats_raw.get("minutesPlayed")
            if not minutes:
                # Suplente que no jugó: no merece la pena guardarlo.
                continue

            mapped: dict[str, float | int] = {}
            for sofa_key, value in stats_raw.items():
                target = _PLAYER_STAT_MAP.get(sofa_key)
                if target is None:
                    continue
                if value is None:
                    continue
                if target in _INT_STAT_KEYS:
                    try:
                        mapped[target] = int(value)
                    except (TypeError, ValueError):
                        continue
                else:
                    try:
                        mapped[target] = float(value)
                    except (TypeError, ValueError):
                        continue

            substitute = bool(entry.get("substitute"))
            out[side].append({
                "external_id": str(ext_id),
                "name": player.get("name") or player.get("shortName") or "",
                "short_name": player.get("shortName"),
                "position": entry.get("position") or player.get("position"),
                "is_starter": not substitute,
                "is_substitute": substitute,
                "shirt_number": entry.get("shirtNumber"),
                "stats": mapped,
            })
    return out


def parse_event_card_counts(incidents_payload: dict) -> dict[str, dict[str, int]]:
    """A partir del feed ``/event/{id}/incidents`` cuenta tarjetas por jugador.

    Devuelve ``{player_external_id: {"yellow_cards": n, "red_cards": m}}``.
    Convención Sofascore:

    * ``incidentClass`` = ``"yellow"`` ⇒ amarilla.
    * ``incidentClass`` = ``"yellowRed"`` ⇒ doble amarilla = roja indirecta.
    * ``incidentClass`` = ``"red"`` ⇒ roja directa.
    """
    counts: dict[str, dict[str, int]] = {}
    for inc in incidents_payload.get("incidents", []):
        if inc.get("incidentType") != "card":
            continue
        player = inc.get("player") or {}
        ext = player.get("id")
        if ext is None:
            continue
        ext_str = str(ext)
        bucket = counts.setdefault(ext_str, {"yellow_cards": 0, "red_cards": 0})
        cls = inc.get("incidentClass")
        if cls == "yellow":
            bucket["yellow_cards"] += 1
        elif cls in ("red", "yellowRed"):
            bucket["red_cards"] += 1
    return counts


def fetch_finished_events(
    tournament_id: int,
    season_id: int,
    *,
    max_pages: int = 5,
    sleep_seconds: float = RATE_LIMIT_SLEEP_SECONDS,
) -> list[dict]:
    """Descarga partidos finalizados de la temporada, paginados.

    El endpoint ``/events/last/{page}`` devuelve los partidos pasados ordenados
    por timestamp (más recientes primero). Cada página trae ~30 partidos.
    """
    log = _log()
    events: list[dict] = []
    for page in range(max_pages):
        url = (
            f"{BASE_URL}/unique-tournament/{tournament_id}"
            f"/season/{season_id}/events/last/{page}"
        )
        try:
            payload = _get(url)
        except RuntimeError as exc:
            # 404 al pasarnos de páginas significa "no hay más resultados"
            if "404" in str(exc):
                break
            log.warning("fetch_finished_events page=%d falló: %s", page, exc)
            break
        page_events = payload.get("events", [])
        if not page_events:
            break
        events.extend(page_events)
        if not payload.get("hasNextPage", False):
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return events


def _save_json(path: Path, data: dict, *, metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "data": data}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_league(
    sport_key: str,
    league_name: str,
    tournament_id: int,
    settings: Settings,
    *,
    raw_dir: Path | None = None,
) -> LeagueFetchResult:
    """Captura todas las páginas de una liga (seasons + top players + standings)."""
    log = _log()
    raw_dir = raw_dir or (settings.raw_data_dir / "sofascore")
    result = LeagueFetchResult(
        sport_key=sport_key, league_name=league_name, tournament_id=tournament_id,
    )

    log.info("[%s] cargando temporadas...", league_name)
    try:
        seasons = fetch_seasons(tournament_id)
    except Exception as exc:
        result.error = f"seasons: {exc}"
        log.error("[%s] error en seasons: %s", league_name, exc)
        return result

    if not seasons:
        result.error = "no seasons returned"
        return result

    current = seasons[0]
    result.season_id = current["id"]
    result.season_name = current.get("name") or current.get("year")
    log.info(
        "[%s] season actual: %s (id=%s)",
        league_name, result.season_name, result.season_id,
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_metadata = {
        "league_name": league_name,
        "sport_key": sport_key,
        "tournament_id": tournament_id,
        "season_id": result.season_id,
        "season_name": result.season_name,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "sofascore",
    }

    time.sleep(RATE_LIMIT_SLEEP_SECONDS)
    log.info("[%s] cargando top-players...", league_name)
    try:
        top_players = fetch_top_players(tournament_id, result.season_id)
    except Exception as exc:
        result.error = f"top_players: {exc}"
        log.error("[%s] error en top-players: %s", league_name, exc)
        return result

    top_path = raw_dir / f"{sport_key}__top_players__{timestamp}.json"
    _save_json(top_path, top_players, metadata={**base_metadata, "endpoint": "top_players"})
    result.top_players_path = top_path

    n_categories = len(top_players.get("topPlayers", {}))
    log.info("[%s] top-players guardado (%d categorías)", league_name, n_categories)

    time.sleep(RATE_LIMIT_SLEEP_SECONDS)
    log.info("[%s] cargando standings...", league_name)
    try:
        standings = fetch_standings(tournament_id, result.season_id)
    except Exception as exc:
        log.warning("[%s] standings falló (continuamos sin ellos): %s", league_name, exc)
    else:
        st_path = raw_dir / f"{sport_key}__standings__{timestamp}.json"
        _save_json(st_path, standings, metadata={**base_metadata, "endpoint": "standings"})
        result.standings_path = st_path
        n_rows = sum(len(g.get("rows", [])) for g in standings.get("standings", []))
        log.info("[%s] standings guardado (%d equipos)", league_name, n_rows)

    return result


def fetch_all(
    settings: Settings | None = None,
    leagues: dict[str, tuple[str, int]] | None = None,
) -> list[LeagueFetchResult]:
    """Captura las 5 grandes ligas (o las que se pasen)."""
    settings = settings or get_settings()
    leagues = leagues or TOURNAMENTS

    results: list[LeagueFetchResult] = []
    for sport_key, (league_name, tournament_id) in leagues.items():
        results.append(fetch_league(sport_key, league_name, tournament_id, settings))
        time.sleep(RATE_LIMIT_SLEEP_SECONDS)
    return results


def _imprimir_resumen(resultados: list[LeagueFetchResult]) -> None:
    log = _log()
    log.info("=" * 80)
    log.info("RESUMEN SCRAPING SOFASCORE")
    log.info("=" * 80)
    n_ok = sum(1 for r in resultados if r.ok)
    for r in resultados:
        estado = "OK " if r.ok else "ERR"
        season = r.season_name or "-"
        tp = r.top_players_path.name if r.top_players_path else "-"
        st = r.standings_path.name if r.standings_path else "-"
        log.info("  [%s] %-20s season=%s", estado, r.league_name, season)
        log.info("         top_players: %s", tp)
        log.info("         standings  : %s", st)
        if r.error:
            log.info("         ERROR: %s", r.error)
    log.info("-" * 80)
    log.info("Ligas OK / total: %d / %d", n_ok, len(resultados))
    log.info("=" * 80)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scraper Sofascore (stats de jugadores y equipos).")
    parser.add_argument(
        "--liga", choices=list(TOURNAMENTS.keys()), default=None,
        help="Captura solo una liga (sport_key).",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.liga:
        leagues = {args.liga: TOURNAMENTS[args.liga]}
    else:
        leagues = TOURNAMENTS

    resultados = fetch_all(settings=settings, leagues=leagues)
    _imprimir_resumen(resultados)
    return 0 if all(r.ok for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
