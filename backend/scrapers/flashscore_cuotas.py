"""Comparador de cuotas de Flashscore: las cuotas de ~13 casas españolas por partido.

Es la pestaña «Cuotas» de la página de partido. Una sola petición GraphQL devuelve **todas las
casas y todos los mercados** del partido (reconocimiento del 2026-11-13, ver
``INVESTIGACION_ERRORES_CUOTAS.md`` §9):

* URL: ``https://global.ds.lsapp.eu/odds/pq_graphql?_hash=oce&eventId=<id>&projectId=13
  &geoIpCode=ES&geoIpSubdivisionCode=ESMD``. ``projectId=13`` es el proyecto que ya usamos
  para bajas y stats; con ``geoIpCode=ES`` trae bet365, Winamax.es, Sportium.es, 1xBet.es,
  Versus.es, Betway, Retabet, Interwetten, Codere, bwin.es, 888sport, Speedybet.es y
  William Hill. No necesita ``x-fsign``.
* Respuesta: ``data.findOddsByEventId.odds`` = lista de ``{bookmakerId, bettingType,
  bettingScope, odds: [item…]}``; cada ``item`` trae ``value`` (cuota actual), ``opening``
  (apertura), ``active`` y, según el mercado, ``eventParticipantId``, ``handicap.value``,
  ``selection`` (``OVER``/``UNDER``/``ODD``/``EVEN``), ``bothTeamsToScore``, ``score``
  (``"2:1"``) o ``winner`` (``"1/X"``). Tras el partido, ``result`` (``WON``/``LOST``/…).
* **No trae** hora de actualización por casa: la frescura es la de nuestra lectura (todas las
  casas de una respuesta salen del mismo instante).
* ``eventParticipantId`` es el participante del evento, no el id del equipo: el local es el
  primero de la lista del 1X2 (``[local, visitante, empate]`` en todas las casas observadas).

Este módulo solo descarga y traduce a **claves neutrales** (las mismas que usamos para
Winamax: ``h2h``, ``totals``, ``btts``, ``double_chance``…); no toca la BD.

Hándicaps: Flashscore da a cada participante **su propio** hándicap. Aquí la línea se guarda
siempre **desde el punto de vista del local** (``line``): «visitante −0,5» se guarda como
``outcome_key="away", line=+0.5``, igual que «local +0,5». Así las dos patas de una línea
comparten clave. En el europeo, el empate ya viene con el hándicap del local.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from curl_cffi import requests as cf

from backend.scrapers.flashscore import FLASHSCORE_PROJECT_ID, HEADERS

ODDS_COMPARISON_URL = "https://global.ds.lsapp.eu/odds/pq_graphql"
ODDS_COMPARISON_HASH = "oce"

# (bettingType, bettingScope) → clave de mercado neutral. Las de partido completo coinciden
# con las de Winamax en ``markets``; las de mitades siguen el patrón ``match_first_half_*``.
_PREFIJO_SCOPE = {
    "FULL_TIME": "",
    "FIRST_HALF": "match_first_half_",
    "SECOND_HALF": "match_second_half_",
}
_BASE_MERCADO = {
    "HOME_DRAW_AWAY": "h2h",
    "OVER_UNDER": "totals",
    "BOTH_TEAMS_TO_SCORE": "btts",
    "DOUBLE_CHANCE": "double_chance",
    "DRAW_NO_BET": "draw_no_bet",
    "ASIAN_HANDICAP": "asian_handicap",
    "EUROPEAN_HANDICAP": "european_handicap",
    "CORRECT_SCORE": "correct_score",
    "HALF_FULL_TIME": "half_time_full_time",
    "ODD_OR_EVEN": "goals_odd_even",
}
# Claves que ya existían para Winamax con otro nombre.
_CLAVES_EXISTENTES = {
    ("ODD_OR_EVEN", "FULL_TIME"): "match_total_goals_odd_even",
}


@dataclass(frozen=True)
class CuotaComparador:
    """Una cuota de una casa en el comparador, ya en claves neutrales."""

    bookmaker_fs_id: int
    market_key: str
    outcome_key: str
    line: Decimal | None
    price: Decimal
    opening_price: Decimal | None
    active: bool


@dataclass(frozen=True)
class ComparadorPartido:
    """Resultado de parsear la respuesta ``oce`` de un partido."""

    event_id: str
    bookmakers: dict[int, str]  # id Flashscore → nombre ("bet365", "Winamax.es"…)
    cuotas: list[CuotaComparador]
    descartadas: int  # items que no se pudieron traducir


def fetch_odds_comparison(
    match_id: str,
    *,
    geo: str = "ES",
    subdivision: str = "ESMD",
    project_id: int = FLASHSCORE_PROJECT_ID,
) -> dict:
    """Descarga el comparador de cuotas de un partido (JSON crudo). **Una petición.**"""
    r = cf.get(
        ODDS_COMPARISON_URL,
        params={
            "_hash": ODDS_COMPARISON_HASH,
            "eventId": match_id,
            "projectId": project_id,
            "geoIpCode": geo,
            "geoIpSubdivisionCode": subdivision,
        },
        headers={k: v for k, v in HEADERS.items() if k != "x-fsign"},
        impersonate="chrome",
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} en cuotas {match_id}: {r.text[:160]}")
    return r.json()


def clave_mercado(betting_type: str, betting_scope: str) -> str:
    """``("OVER_UNDER", "FIRST_HALF")`` → ``"match_first_half_totals"``.

    Un tipo desconocido no se pierde: sale como ``fs_<tipo>`` con su prefijo de mitad.
    """
    existente = _CLAVES_EXISTENTES.get((betting_type, betting_scope))
    if existente:
        return existente
    base = _BASE_MERCADO.get(betting_type) or f"fs_{(betting_type or 'desconocido').lower()}"
    prefijo = _PREFIJO_SCOPE.get(betting_scope)
    if prefijo is None:
        prefijo = f"{(betting_scope or 'scope').lower()}_"
    return f"{prefijo}{base}"


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def participantes_local_visitante(odds: list[dict]) -> tuple[str | None, str | None]:
    """Ids de participante (local, visitante) deducidos del 1X2.

    En el 1X2 cada casa lista ``[local, visitante, empate]``; se toma el orden más repetido
    entre casas (y, si no hay 1X2, el del «empate no válido»).
    """
    for tipo in ("HOME_DRAW_AWAY", "DRAW_NO_BET"):
        ordenes: Counter[tuple[str, str]] = Counter()
        for bloque in odds:
            if bloque.get("bettingType") != tipo:
                continue
            ids = [i.get("eventParticipantId") for i in bloque.get("odds") or []]
            ids = [x for x in ids if x]
            if len(ids) == 2 and ids[0] != ids[1]:
                ordenes[(ids[0], ids[1])] += 1
        if ordenes:
            (local, visitante), _ = ordenes.most_common(1)[0]
            return local, visitante
    return None, None


def _resultado(
    betting_type: str, item: dict, local: str | None, visitante: str | None,
) -> tuple[str, Decimal | None] | None:
    """Traduce un item a ``(outcome_key, line)``; ``None`` si no se reconoce."""
    pid = item.get("eventParticipantId")
    lado = "home" if pid and pid == local else "away" if pid and pid == visitante else None
    if pid and lado is None:
        return None
    handicap = _decimal((item.get("handicap") or {}).get("value"))

    if betting_type == "HOME_DRAW_AWAY":
        return (lado or "draw", None)
    if betting_type == "DRAW_NO_BET":
        return (lado, None) if lado else None
    if betting_type == "DOUBLE_CHANCE":
        # Participante = ese equipo o empate; sin participante = «12».
        return ({"home": "1X", "away": "X2"}.get(lado or "", "12"), None)
    if betting_type == "OVER_UNDER":
        sel = (item.get("selection") or "").upper()
        if sel not in {"OVER", "UNDER"} or handicap is None:
            return None
        return (sel.lower(), handicap)
    if betting_type == "BOTH_TEAMS_TO_SCORE":
        btts = item.get("bothTeamsToScore")
        if btts is None:
            return None
        return ("yes" if btts else "no", None)
    if betting_type == "ODD_OR_EVEN":
        sel = (item.get("selection") or "").upper()
        return ({"ODD": "odd", "EVEN": "even"}[sel], None) if sel in {"ODD", "EVEN"} else None
    if betting_type == "CORRECT_SCORE":
        score = (item.get("score") or "").strip()
        return (score, None) if score else None
    if betting_type == "HALF_FULL_TIME":
        winner = (item.get("winner") or "").strip().upper()
        return (winner, None) if winner else None
    if betting_type == "ASIAN_HANDICAP":
        if lado is None or handicap is None:
            return None
        # Línea siempre desde el local: la del visitante se invierte.
        return (lado, handicap if lado == "home" else -handicap)
    if betting_type == "EUROPEAN_HANDICAP":
        if handicap is None:
            return None
        if lado is None:
            return ("draw", handicap)  # el empate ya viene con el hándicap del local
        return (lado, handicap if lado == "home" else -handicap)
    # Tipo desconocido: se guarda con lo que haya para no perder el mercado.
    etiqueta = (
        lado
        or (item.get("selection") or "").lower()
        or (item.get("winner") or item.get("score") or "")
    )
    return (str(etiqueta)[:32], handicap) if etiqueta else None


def parse_odds_comparison(payload: dict) -> ComparadorPartido:
    """Respuesta ``oce`` → cuotas en claves neutrales (sin red ni BD)."""
    root = ((payload or {}).get("data") or {}).get("findOddsByEventId") or {}
    settings = root.get("settings") or {}
    bookmakers: dict[int, str] = {}
    for pb in settings.get("bookmakers") or []:
        bm = pb.get("bookmaker") or {}
        if bm.get("id") is not None and bm.get("name"):
            bookmakers[int(bm["id"])] = str(bm["name"])

    odds = root.get("odds") or []
    local, visitante = participantes_local_visitante(odds)
    cuotas: list[CuotaComparador] = []
    vistos: set[tuple] = set()
    descartadas = 0
    for bloque in odds:
        bm_id = bloque.get("bookmakerId")
        tipo = bloque.get("bettingType") or ""
        scope = bloque.get("bettingScope") or ""
        if bm_id is None:
            continue
        market_key = clave_mercado(tipo, scope)
        for item in bloque.get("odds") or []:
            price = _decimal(item.get("value"))
            res = _resultado(tipo, item, local, visitante)
            if price is None or price <= 1 or res is None:
                descartadas += 1
                continue
            outcome_key, line = res
            clave = (int(bm_id), market_key, outcome_key, line)
            if clave in vistos:  # algunas casas repiten un item; nos quedamos con el primero
                descartadas += 1
                continue
            vistos.add(clave)
            opening = _decimal(item.get("opening"))
            cuotas.append(CuotaComparador(
                bookmaker_fs_id=int(bm_id),
                market_key=market_key,
                outcome_key=outcome_key,
                line=line,
                price=price,
                opening_price=opening if opening is not None and opening > 1 else None,
                active=bool(item.get("active", True)),
            ))
    return ComparadorPartido(
        event_id=str(root.get("eventId") or ""),
        bookmakers=bookmakers,
        cuotas=cuotas,
        descartadas=descartadas,
    )


__all__ = [
    "ComparadorPartido",
    "CuotaComparador",
    "clave_mercado",
    "fetch_odds_comparison",
    "parse_odds_comparison",
    "participantes_local_visitante",
]
