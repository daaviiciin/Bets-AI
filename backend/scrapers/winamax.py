"""Scraper de Winamax España (apuestas-deportivas).

Winamax es una SPA pero el HTML inicial pre-renderiza un objeto JSON
gigante en una variable JavaScript ``PRELOADED_STATE``. Esto nos permite
extraer todos los markets y cuotas de un partido con UNA sola petición
HTTP, sin necesidad de Playwright/Selenium.

Estructura del PRELOADED_STATE
------------------------------

* ``matches[matchId]``    metadatos del partido (equipos, kickoff, sportId).
* ``bets[betId]``         un mercado: ``betType`` (int, identificador del
                          tipo de mercado), ``betTitle`` (str, título), 
                          ``outcomes`` (list[outcomeId]),
                          ``specialBetValue`` (str con line/player/etc).
* ``outcomes[outcomeId]`` selección concreta dentro de un mercado:
                          ``label`` (texto, "Más de 2,5" o "Bukayo Saka 2+"),
                          ``code`` (sub-tipo: ``over``/``under``,
                          ``pre:playerprops:...``).
* ``odds[outcomeId]``     cuota decimal (float).

Catálogo de partidos
--------------------

El HTML de **cualquier** página solo pre-renderiza los ~50 eventos de la portada
(unos 20 de fútbol). El resto lo pide la web por **Socket.IO** (``BETTING_CONFIGURATION``
en el HTML: servidor ``sports-eu-west-3.winamax.es``, ruta ``/uof-sports-server/socket.io``,
Engine.IO v3). :func:`fetch_calendar` hace lo mismo que la página "Calendario" de la web:
emite ``["m", {"route": "calendar:24"}]`` y recibe los partidos de las próximas 24 h de
todos los deportes, con el mismo formato que ``PRELOADED_STATE["matches"]``. Comprobado
el 2026-10-15: 590 partidos (120 de fútbol), 36/36 de los nuestros de ese día.

Para ir más allá de 24 h, :func:`fetch_tournaments` hace lo que la página de una competición:
``["m", {"route": "tournament:36"}]`` (LaLiga; visto en DevTools el 2026-10-16) devuelve todos
los partidos de esa liga con cuota publicada, aunque sean de dentro de varios días. Todas las
rutas van por una sola conexión (:class:`SocketRouteCollector` empareja por ``requestId``).

Cuando ``moreBets`` > nº de bets cargadas en la home, hay que pedir
explícitamente la URL del partido para conseguir los 500+ mercados con
player props.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from curl_cffi import requests as cf

BASE_URL = "https://www.winamax.es/apuestas-deportivas"
REQUEST_TIMEOUT = 30
# Socket.IO de la web (``BETTING_CONFIGURATION.SOCKET_IO_SERVER`` + ``SOCKET_IO_PATH``).
SOCKET_URL = "wss://sports-eu-west-3.winamax.es/uof-sports-server/socket.io/"
# Versión de su web que va en la URL del socket (vista en DevTools el 2026-10-16). Sin ella
# también responde; se manda para pedir lo mismo que un navegador.
SOCKET_CLIENT_VERSION = "3.71.0"
SOCKET_TIMEOUT = 20.0
SOCKET_MAX_BYTES = 50 * 1024 * 1024  # calendar:24 ocupa ~1 MB
SPORT_FOOTBALL = 1
SPORT_BASKETBALL = 2

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# ---------------------------------------------------------------------------
# Mapeo de betType (int) → market_key canónico (string).
# Sólo mantenemos los mercados que de verdad nos interesan para value betting.
# El resto se ignora (Winamax tiene 500+ tipos, muchos exóticos).
# ---------------------------------------------------------------------------
MARKET_TYPES: dict[int, str] = {
    # 1X2 / outcome del partido
    3178: "match_winner",                  # "Resultado"
    3536: "match_winner_dnb",              # "Ganador sin empate"
    3072: "match_double_chance",           # "Doble oportunidad"
    # Goles
    2749: "match_totals",                  # "Número total de goles"
    3019: "match_total_goals_odd_even",
    3133: "match_btts",                    # "Ambos equipos marcan"
    3246: "match_winner_and_totals",
    # Specials por equipo
    2615: "team_home_totals_goals",
    2680: "team_away_totals_goals",
    # Tarjetas / córneres / tiros (a nivel partido)
    2603: "match_cards_totals",
    3621: "match_corners_totals",
    6202: "match_shots_totals",
    6205: "match_sot_totals",
    # Primera mitad (IDs Winamax.es observados mayo 2026)
    2531: "match_first_half_totals",
    2394: "team_home_first_half_totals_goals",
    2801: "team_away_first_half_totals_goals",
    3814: "match_first_half_corners_totals",
    3210: "match_first_half_cards_totals",
    # ---- PLAYER PROPS ----
    # Pestaña "Estad. jugadores" (vistos en Winamax.es el 2026-10-19): un mercado por
    # escalón ("Realiza 2 o más remates a puerta", ``specialBetValue="count=2"``) y un
    # outcome por jugador (``label`` = nombre, ``code`` = ``sr:player:<id>``).
    6858: "player_shots_alt",              # Realiza N o más remates
    6864: "player_sot_alt",                # Realiza N o más remates a puerta
    6921: "player_saves_alt",              # Realiza N o más paradas (porteros)
    6933: "player_card_yes_no",            # El jugador recibe una o más tarjetas
    # Ids de mayo 2026 (ver LEGACY_MARKET_TYPE_IDS). "logros" = grid alternativo con
    # líneas 1+/2+/3+ múltiples por jugador.
    2747: "player_shots_alt",              # Tiros del jugador (alternativas)
    3311: "player_sot_alt",                # Tiros a puerta del jugador (alt)
    3304: "player_passes_alt",             # Pases del jugador (alt)
    3465: "player_tackles_alt",            # Tackles del jugador (alt)
    3361: "player_assists_alt",            # Asistencias (alt)
    # versiones single-line over/under (más frecuentes en pre-match)
    5712: "player_shots",                  # Tiros del jugador
    5720: "player_sot",                    # Tiros a puerta del jugador
    # tarjetas
    5711: "player_card_yes_no",            # Recibe ≥1 tarjeta
    # goles
    2705: "player_anytime_scorer",         # Goleador (any time)
    3876: "player_to_score_2plus",         # Marca 2 goles o más
    4189: "player_first_scorer",
    4190: "player_last_scorer",
    4191: "player_scorer_and_team_wins",
}

# Subset de markets que son "player props" (clave para apuestas individuales
# por jugador). Útil para filtrar rápido sin recorrer todo el catálogo.
PLAYER_PROP_MARKETS: frozenset[str] = frozenset({
    "player_shots", "player_sot", "player_shots_alt", "player_sot_alt",
    "player_saves_alt",
    "player_passes_alt", "player_tackles_alt", "player_assists_alt",
    "player_card_yes_no",
    "player_anytime_scorer", "player_to_score_2plus",
    "player_first_scorer", "player_last_scorer", "player_scorer_and_team_wins",
})


# Ids antiguos que comparten clave con los actuales (6858/6864/6933). Winamax ya no los
# publica (muestra del 2026-10-19); se mantienen por si vuelven a aparecer.
LEGACY_MARKET_TYPE_IDS: frozenset[int] = frozenset({2747, 3311, 5711})

# Mercados de jugador publicados como escalones "N o más" (``count=N``).
COUNT_LADDER_MARKETS: frozenset[str] = frozenset({
    "player_shots_alt", "player_sot_alt", "player_saves_alt",
})


def _log() -> logging.Logger:
    return logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de bajo nivel
# ---------------------------------------------------------------------------


def _extract_balanced_json(text: str, start: int) -> str:
    """Extrae el primer objeto JSON desde ``start`` balanceando llaves.

    Maneja correctamente strings con llaves dentro y backslash escapes.
    Lanza :class:`RuntimeError` si el objeto no cierra.
    """
    if start >= len(text) or text[start] != "{":
        raise RuntimeError("_extract_balanced_json: no apunta a un '{'")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise RuntimeError("JSON sin cerrar en PRELOADED_STATE")


def fetch_state(url: str) -> dict:
    """Descarga ``url`` y devuelve el dict ``PRELOADED_STATE`` parseado."""
    r = cf.get(url, headers=_HEADERS, impersonate="chrome", timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} en {url}: {r.text[:200]}")
    m = re.search(r"PRELOADED_STATE\s*=\s*", r.text)
    if not m:
        raise RuntimeError("No se encontró 'PRELOADED_STATE' en la respuesta")
    payload = _extract_balanced_json(r.text, m.end())
    return json.loads(payload)


# ---------------------------------------------------------------------------
# Funciones públicas de descarga
# ---------------------------------------------------------------------------


def fetch_football_overview() -> dict:
    """Descarga la home de fútbol con la lista de partidos disponibles."""
    return fetch_sport_overview(SPORT_FOOTBALL)


def fetch_basketball_overview() -> dict:
    return fetch_sport_overview(SPORT_BASKETBALL)


def fetch_sport_overview(sport_id: int) -> dict:
    """Home de un deporte (1=fútbol, 2=basket en Winamax.es, verificar otros)."""
    return fetch_state(f"{BASE_URL}/sport/{sport_id}")


def fetch_match(tournament_id: int | str, match_id: int | str) -> dict:
    """Descarga el detalle completo de un partido (todos sus mercados)."""
    url = f"{BASE_URL}/match/{tournament_id}/{match_id}"
    return fetch_state(url)


def parse_socketio_event(frame: str) -> tuple[str, dict] | None:
    """Frame Engine.IO v3 ``42["evento", {...}]`` → ``("evento", {...})``; otro tipo → ``None``."""
    if not frame.startswith("42"):
        return None
    try:
        data = json.loads(frame[2:])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], dict):
        return None
    return str(data[0]), data[1]


class SocketRouteCollector:
    """Protocolo de una conexión que pide varias rutas (sin red, para poder testearlo).

    Por cada frame del servidor, :meth:`on_frame` dice qué contestar: ``2`` (ping) → ``3``;
    ``40`` (conectado) → una petición ``42["m", {"route", "requestId"}]`` por ruta. Las
    respuestas se guardan en :attr:`responses` según su ``requestId``.
    """

    def __init__(self, routes: Iterable[str], *, new_request_id=None) -> None:
        import uuid

        make_id = new_request_id or (lambda: uuid.uuid4().hex)
        self._route_by_id = {make_id(): route for route in dict.fromkeys(routes)}
        self._sent = False
        self.responses: dict[str, dict] = {}

    @property
    def pending(self) -> list[str]:
        """Rutas que aún no han recibido respuesta."""
        return [r for r in self._route_by_id.values() if r not in self.responses]

    def on_frame(self, frame: str) -> list[str]:
        """Procesa un frame recibido y devuelve los frames que hay que enviar."""
        if frame == "2":
            return ["3"]
        if frame.startswith("40"):
            if self._sent:
                return []
            self._sent = True
            return [
                "42" + json.dumps(["m", {"route": route, "requestId": rid}])
                for rid, route in self._route_by_id.items()
            ]
        event = parse_socketio_event(frame)
        if event and event[0] == "m":
            route = self._route_by_id.get(event[1].get("requestId"))
            if route is not None:
                self.responses[route] = event[1]
        return []


async def _fetch_socket_routes(routes: Iterable[str], *, timeout: float) -> dict[str, dict]:
    """Una conexión: pide todas las ``routes`` y devuelve ``ruta → respuesta``.

    Las rutas que no respondan en ``timeout`` segundos no aparecen en el resultado.
    """
    import websockets

    collector = SocketRouteCollector(routes)
    url = (
        f"{SOCKET_URL}?language=ES&version={SOCKET_CLIENT_VERSION}"
        "&embed=false&EIO=3&transport=websocket"
    )
    headers = {"Origin": "https://www.winamax.es", "User-Agent": _HEADERS["User-Agent"]}
    async with websockets.connect(
        url, additional_headers=headers, open_timeout=timeout, max_size=SOCKET_MAX_BYTES,
    ) as ws:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while collector.pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                frame = str(await asyncio.wait_for(ws.recv(), remaining))
            except asyncio.TimeoutError:
                break
            for out in collector.on_frame(frame):
                await ws.send(out)
    return collector.responses


def _run_sync(coro):
    """Ejecuta ``coro`` desde código síncrono, aunque ya haya un bucle de asyncio en marcha."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Llamado desde código asíncrono: se ejecuta en un hilo con su propio bucle.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def fetch_calendar(hours: int = 24, *, timeout: float = SOCKET_TIMEOUT) -> dict:
    """Partidos de las próximas ``hours`` horas (todos los deportes) vía Socket.IO.

    Devuelve un dict con ``matches`` en el mismo formato que ``PRELOADED_STATE``, apto para
    :func:`parse_football_matches`. Una conexión de pocos segundos por llamada.
    """
    route = f"calendar:{hours}"
    responses = _run_sync(_fetch_socket_routes([route], timeout=timeout))
    if route not in responses:
        raise RuntimeError(f"Socket Winamax: sin respuesta a '{route}' en {timeout:.0f}s")
    return responses[route]


def fetch_tournaments(
    tournament_ids: Iterable[int | str], *, timeout: float = SOCKET_TIMEOUT,
) -> dict[str, dict]:
    """Partidos de cada torneo con cuota publicada, como la página de la competición.

    Todas las ligas por una sola conexión. Devuelve ``tournamentId`` (str) → respuesta en el
    formato de ``PRELOADED_STATE`` (``matches``, ``bets``, ``outcomes``…); los torneos que no
    respondan a tiempo no aparecen.
    """
    ids = [str(t) for t in tournament_ids]
    responses = _run_sync(
        _fetch_socket_routes([f"tournament:{t}" for t in ids], timeout=timeout),
    )
    return {t: responses[f"tournament:{t}"] for t in ids if f"tournament:{t}" in responses}


# ---------------------------------------------------------------------------
# Parsers tipados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WinamaxMatchInfo:
    """Metadatos de un partido en Winamax (versión normalizada)."""
    match_id: str
    title: str
    home_team: str
    away_team: str
    tournament_id: str
    tournament_name: str | None
    sport_id: int
    kickoff: datetime
    sr_tournament_id: str | None  # ej. sr:tournament:8 (Sportradar vía Winamax)
    home_competitor_id: str | None
    away_competitor_id: str | None
    more_bets: int


@dataclass(frozen=True)
class WinamaxOutcome:
    """Una opción concreta dentro de un mercado (con su cuota)."""
    outcome_id: str
    label: str
    odds: float
    code: str | None = None


@dataclass(frozen=True)
class WinamaxBet:
    """Un mercado entero (varios outcomes con sus cuotas)."""
    bet_id: str
    market_key: str            # nuestra key canónica
    bet_type_id: int
    title: str
    special_bet_value: str | None
    outcomes: tuple[WinamaxOutcome, ...]


@dataclass(frozen=True)
class WinamaxPlayerProp:
    """Una cuota concreta sobre un jugador (línea + sentido).

    Pensado como nivel "leg de apuesta" individual, listo para combinar
    con nuestro modelo Player y nuestras stats por partido.
    """
    bet_id: str
    market_key: str            # p.ej. "player_sot" / "player_shots_alt"
    player_name: str
    player_external_id: str | None  # id Sportradar (si está en specialBetValue)
    line: float | None         # 0.5, 1.5, 2.0, ...
    side: str                  # "over" / "under" / "yes" / "no" / "bare"
    odds: float
    outcome_id: str
    raw_label: str             # texto original ("Bukayo Saka 2+")


# ---------------------------------------------------------------------------
# Implementación
# ---------------------------------------------------------------------------


def parse_football_matches(state: dict) -> list[WinamaxMatchInfo]:
    """Extrae los partidos de fútbol con metadatos básicos."""
    return parse_sport_matches(state, SPORT_FOOTBALL)


def parse_sport_matches(state: dict, sport_id: int) -> list[WinamaxMatchInfo]:
    """Variante parametrizada por ``sport_id``."""
    matches = state.get("matches", {}) or {}
    tournaments = state.get("tournaments", {}) or {}
    out: list[WinamaxMatchInfo] = []
    for raw_id, m in matches.items():
        if m.get("sportId") != sport_id:
            continue
        tour_id = m.get("tournamentId")
        tour = tournaments.get(str(tour_id)) or tournaments.get(tour_id) or {}
        kickoff_ts = m.get("matchStart")
        try:
            kickoff = datetime.fromtimestamp(int(kickoff_ts), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
        sr_raw = m.get("srTournamentId")
        out.append(WinamaxMatchInfo(
            match_id=str(m.get("matchId")),
            title=str(m.get("title") or ""),
            home_team=str(m.get("competitor1Name") or ""),
            away_team=str(m.get("competitor2Name") or ""),
            tournament_id=str(tour_id) if tour_id is not None else "",
            tournament_name=tour.get("tournamentName"),
            sport_id=int(sport_id),
            kickoff=kickoff,
            sr_tournament_id=str(sr_raw) if sr_raw else None,
            home_competitor_id=(
                str(m["competitor1Id"]) if m.get("competitor1Id") is not None else None
            ),
            away_competitor_id=(
                str(m["competitor2Id"]) if m.get("competitor2Id") is not None else None
            ),
            more_bets=int(m.get("moreBets") or 0),
        ))
    out.sort(key=lambda x: (x.kickoff, x.match_id))
    return out


def parse_match_bets(state: dict, match_id: int | str) -> list[WinamaxBet]:
    """Devuelve los mercados de un partido como lista de :class:`WinamaxBet`.

    Sólo incluye los mercados cuyo ``betType`` está en :data:`MARKET_TYPES`.
    El resto se filtra silenciosamente.
    """
    bets = state.get("bets", {}) or {}
    outcomes = state.get("outcomes", {}) or {}
    odds = state.get("odds", {}) or {}
    target_id = str(match_id)

    out: list[WinamaxBet] = []
    for bid, bet in bets.items():
        if str(bet.get("matchId")) != target_id:
            continue
        bt_id = bet.get("betType")
        if not isinstance(bt_id, int):
            continue
        market_key = MARKET_TYPES.get(bt_id)
        if market_key is None:
            continue

        outcome_objs: list[WinamaxOutcome] = []
        for oid in bet.get("outcomes", []) or []:
            oc = outcomes.get(str(oid)) or outcomes.get(oid) or {}
            cuota_raw = odds.get(str(oid))
            if cuota_raw is None:
                cuota_raw = odds.get(oid)
            if cuota_raw is None:
                continue
            try:
                cuota = float(cuota_raw)
            except (TypeError, ValueError):
                continue
            label = str(oc.get("label") or "")
            outcome_objs.append(WinamaxOutcome(
                outcome_id=str(oid),
                label=label,
                odds=cuota,
                code=oc.get("code"),
            ))

        if not outcome_objs:
            continue

        out.append(WinamaxBet(
            bet_id=str(bid),
            market_key=market_key,
            bet_type_id=bt_id,
            title=str(bet.get("betTitle") or ""),
            special_bet_value=bet.get("specialBetValue"),
            outcomes=tuple(outcome_objs),
        ))
    return out


# Regex para parsear:
#   "player=sr:player:85859|total=1.5"    → player_id, total
_RE_SBV_PLAYER = re.compile(r"player=sr:player:(?P<pid>\d+)")
_RE_SBV_TOTAL = re.compile(r"total=(?P<total>[\d.]+)")
# variante "alt": specialBetValue=variant=pre:playerprops:{matchId}:{playerSrId}
_RE_SBV_VARIANT = re.compile(
    r"variant=pre:playerprops:(?P<match>\d+):(?P<pid>\d+)"
)
# outcome.code para player_props_alt:
#   pre:playerprops:69339436:85859:1
_RE_CODE_ALT = re.compile(
    r"pre:playerprops:(?P<match>\d+):(?P<pid>\d+):(?P<line>\d+)"
)
# Escalones "Realiza N o más …": specialBetValue="count=N", outcome.code="sr:player:<id>".
_RE_SBV_COUNT = re.compile(r"(?:^|\|)count=(?P<count>\d+)")
_RE_SBV_GOALS = re.compile(r"(?:^|\|)goals=(?P<goals>\d+)")
_RE_CODE_PLAYER = re.compile(r"^sr:player:(?P<pid>\d+)$")
# outcome.label para single line:  "Antoine Griezmann más de 1,5"
_RE_LABEL_OVER_UNDER = re.compile(
    r"^(?P<name>.+?)\s+(?P<side>m[áa]s|menos)\s+de\s+(?P<line>[\d,\.]+)\s*$",
    re.IGNORECASE,
)
# outcome.label para alt: "Bukayo Saka 2+"
_RE_LABEL_ALT = re.compile(
    r"^(?P<name>.+?)\s+(?P<line>\d+)\+\s*$"
)


def _parse_es_decimal(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_player_props(bets: Iterable[WinamaxBet]) -> list[WinamaxPlayerProp]:
    """A partir de la lista cruda de bets, extrae cuotas tipo "Bukayo Saka SoT 1+".

    Filtra automáticamente a los mercados de jugador (``PLAYER_PROP_MARKETS``)
    y normaliza ``line`` y ``side`` para que sean directamente consumibles.
    """
    out: list[WinamaxPlayerProp] = []
    for bet in bets:
        if bet.market_key not in PLAYER_PROP_MARKETS:
            continue

        # Player ID externo (sportradar) si existe en el specialBetValue.
        sbv = bet.special_bet_value or ""
        m_pid = _RE_SBV_PLAYER.search(sbv) or _RE_SBV_VARIANT.search(sbv)
        bet_player_pid = m_pid.group("pid") if m_pid else None

        # Línea del bet entero (single-line) si está en el sbv.
        m_total = _RE_SBV_TOTAL.search(sbv)
        bet_line = _parse_es_decimal(m_total.group("total")) if m_total else None
        # 3876 publica "Marca 2 / 3 / 4 goles o más" (goals=N) con el mismo betType: solo el
        # de 2 es ``player_to_score_2plus``; los demás se descartan.
        if bet.market_key == "player_to_score_2plus":
            m_goals = _RE_SBV_GOALS.search(sbv)
            if m_goals and m_goals.group("goals") != "2":
                continue
        # Escalón "N o más" (tiros, tiros a puerta, paradas): count=N.
        m_count = _RE_SBV_COUNT.search(sbv)
        bet_count = int(m_count.group("count")) if m_count else None

        for oc in bet.outcomes:
            label = oc.label.strip()
            code = oc.code or ""

            # Caso 1: single line con código over/under.
            if code in ("over", "under"):
                m = _RE_LABEL_OVER_UNDER.match(label)
                if not m:
                    continue
                name = m.group("name").strip()
                line_str = m.group("line")
                line = _parse_es_decimal(line_str) or bet_line
                side = "over" if code == "over" else "under"
                out.append(WinamaxPlayerProp(
                    bet_id=bet.bet_id,
                    market_key=bet.market_key,
                    player_name=name,
                    player_external_id=bet_player_pid,
                    line=line,
                    side=side,
                    odds=oc.odds,
                    outcome_id=oc.outcome_id,
                    raw_label=label,
                ))
                continue

            # Caso 2a: escalón "Realiza N o más …" → un outcome por jugador, "N+" = más de
            # N - 0,5. El id de Sportradar viene en el code del outcome.
            if bet_count is not None and bet.market_key in COUNT_LADDER_MARKETS:
                if not label:
                    continue
                m_pid = _RE_CODE_PLAYER.match(code)
                out.append(WinamaxPlayerProp(
                    bet_id=bet.bet_id,
                    market_key=bet.market_key,
                    player_name=label,
                    player_external_id=m_pid.group("pid") if m_pid else bet_player_pid,
                    line=bet_count - 0.5,
                    side="over",
                    odds=oc.odds,
                    outcome_id=oc.outcome_id,
                    raw_label=label,
                ))
                continue

            # Caso 2b: alt grid "Bukayo Saka 2+"  con code "pre:playerprops:...:N"
            m_code = _RE_CODE_ALT.match(code)
            m_label = _RE_LABEL_ALT.match(label)
            if m_code or m_label:
                line_int = None
                if m_code:
                    line_int = int(m_code.group("line"))
                elif m_label:
                    line_int = int(m_label.group("line"))
                # Para "N+", la línea over/under equivalente es N - 0.5.
                line_ou = (line_int - 0.5) if line_int is not None else None
                name = (m_label.group("name").strip()
                        if m_label else label.split(" ", 1)[0])
                pid = (m_code.group("pid") if m_code else bet_player_pid)
                out.append(WinamaxPlayerProp(
                    bet_id=bet.bet_id,
                    market_key=bet.market_key,
                    player_name=name,
                    player_external_id=pid,
                    line=line_ou,
                    side="over",   # "N+" siempre es "over" lógicamente
                    odds=oc.odds,
                    outcome_id=oc.outcome_id,
                    raw_label=label,
                ))
                continue

            # Caso 3: anytime / yes-no (sin línea)
            if bet.market_key in (
                "player_anytime_scorer", "player_to_score_2plus",
                "player_card_yes_no", "player_first_scorer",
                "player_last_scorer", "player_scorer_and_team_wins",
            ):
                # label = "Bukayo Saka" (sin "más/menos") → bet "yes" implícito
                out.append(WinamaxPlayerProp(
                    bet_id=bet.bet_id,
                    market_key=bet.market_key,
                    player_name=label,
                    player_external_id=bet_player_pid,
                    line=None,
                    side="yes",
                    odds=oc.odds,
                    outcome_id=oc.outcome_id,
                    raw_label=label,
                ))
                continue

    return out


__all__ = [
    "BASE_URL",
    "COUNT_LADDER_MARKETS",
    "LEGACY_MARKET_TYPE_IDS",
    "MARKET_TYPES",
    "PLAYER_PROP_MARKETS",
    "SPORT_BASKETBALL",
    "SPORT_FOOTBALL",
    "WinamaxBet",
    "WinamaxMatchInfo",
    "WinamaxOutcome",
    "WinamaxPlayerProp",
    "fetch_football_overview",
    "fetch_basketball_overview",
    "fetch_match",
    "fetch_state",
    "parse_football_matches",
    "parse_match_bets",
    "parse_sport_matches",
    "parse_player_props",
]
