"""Cliente para la API interna de Flashscore.

**Estado**: POC funcional. Funciona el feed global de fixtures del día y el
summary por partido. Las stats individuales y lineups requieren partidos ya
jugados (campos vacíos para futuros). El endpoint de odds (formato URL) está
pendiente de calibrar.

Notas técnicas
--------------
* Dominio API: ``https://global.flashscore.ninja/2/x/feed/{path}``.
* Cabecera obligatoria: ``x-fsign: SW9D1eZo`` (token estable observado en
  enero 2026; si se rota habrá que extraerlo de los bundles JS).
* Pasamos ``impersonate="chrome"`` para mimetizar el handshake TLS y pasar
  el bot detection.
* Formato propietario: cada registro está separado por ``¬`` y los pares
  clave-valor por ``÷``. Ejemplo::

      AA÷nJOzRmJ8¬AD÷1777906800¬CX÷Skenderbeu¬AE÷Tirana¬...

  Las claves más relevantes están mapeadas en ``KEY_MAP``.

Uso CLI
-------
::

    python -m backend.scrapers.flashscore --fixtures-today
    python -m backend.scrapers.flashscore --match-summary nJOzRmJ8
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from curl_cffi import requests as cf

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import Settings, get_settings

BASE_URL = "https://global.flashscore.ninja/2/x/feed"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.flashscore.com/",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "x-fsign": "SW9D1eZo",
}

# Mapeo de claves observadas en el feed (no exhaustivo, ampliar al usar)
KEY_MAP = {
    "SA": "sport_id",
    "ZA": "league_full_name",
    "ZB": "league_id",
    "ZC": "league_external_id",
    "ZD": "country_code_short",
    "ZE": "season_id",
    "ZH": "league_country_id",
    "ZL": "league_url_path",
    "AA": "match_id",
    "AD": "scheduled_at_epoch",
    "AB": "status_code",
    "AC": "round_or_extra",
    "CX": "home_team_name",
    "AE": "home_team_alias",
    "FH": "home_team_alias_2",
    "AF": "away_team_name",
    "FK": "away_team_alias",
    "WM": "home_short_code",
    "WN": "away_short_code",
    "WU": "home_team_slug",
    "WV": "away_team_slug",
    "OA": "home_team_logo",
    "OB": "away_team_logo",
    "AG": "score_home",
    "AH": "score_away",
    "AI": "is_finished",
    "AT": "minute",
    "AW": "home_indicator",
    "JA": "broadcast_info",
}


@dataclass
class FlashscoreMatch:
    """Match parseado del feed propietario."""

    match_id: str
    league_full_name: str | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None
    scheduled_at: datetime | None = None
    score_home: int | None = None
    score_away: int | None = None
    is_finished: bool = False
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "league": self.league_full_name,
            "home_team": self.home_team_name,
            "away_team": self.away_team_name,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "score_home": self.score_home,
            "score_away": self.score_away,
            "is_finished": self.is_finished,
        }


def _http_get(path: str) -> str:
    url = f"{BASE_URL}/{path.lstrip('/')}"
    r = cf.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} en {url}: {r.text[:160]}")
    return r.text


def parse_payload(text: str) -> list[dict[str, str]]:
    """Parsea el formato ``KEY÷value¬KEY÷value¬...`` separando por ``~`` los registros.

    El feed contiene registros agrupados por liga (cabeceras con ``ZA``, ``ZB``...)
    seguidos de matches (cabeceras con ``AA``). Los grupos se separan por ``~``.
    Devolvemos una lista de dicts crudos (sin remap de claves).
    """
    records: list[dict[str, str]] = []
    for chunk in text.split("~"):
        chunk = chunk.strip()
        if not chunk:
            continue
        record: dict[str, str] = {}
        for pair in chunk.split("¬"):
            if "÷" not in pair:
                continue
            key, _, value = pair.partition("÷")
            record[key] = value
        if record:
            records.append(record)
    return records


def fetch_fixtures_feed(
    *, day_offset: int = 0, sport: int = 1, locale: str = "en",
) -> list[FlashscoreMatch]:
    """Obtiene los partidos del día (offset 0) o del día siguiente (offset +1)."""
    path = f"f_{sport}_{day_offset}_3_{locale}_1"
    text = _http_get(path)
    records = parse_payload(text)

    league_context: dict[str, str] = {}
    matches: list[FlashscoreMatch] = []
    for rec in records:
        if "ZA" in rec:  # cabecera de liga
            league_context = {KEY_MAP.get(k, k): v for k, v in rec.items()}
            continue
        if "AA" not in rec:
            continue
        scheduled = None
        epoch = rec.get("AD")
        if epoch and epoch.isdigit():
            scheduled = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        # Flashscore status codes (campo AB):
        #   1 = scheduled, 2 = live (1st half), 3 = finished, 4 = HT,
        #   5 = live (2nd half), 6 = ET, 7 = penalties, 8 = postponed, ...
        status_code = rec.get("AB")
        is_finished = status_code == "3" or rec.get("AI") in {"1", "y"}
        matches.append(FlashscoreMatch(
            match_id=rec["AA"],
            league_full_name=league_context.get("league_full_name"),
            home_team_name=rec.get("CX"),
            away_team_name=rec.get("AF"),
            scheduled_at=scheduled,
            score_home=_to_int_or_none(rec.get("AG")),
            score_away=_to_int_or_none(rec.get("AH")),
            is_finished=is_finished,
            raw=rec,
        ))
    return matches


def fetch_match_summary(match_id: str) -> dict[str, str]:
    """Devuelve el bloque de summary parseado para un match."""
    text = _http_get(f"dc_2_{match_id}")
    records = parse_payload(text)
    if not records:
        return {}
    return records[0]


def fetch_match_statistics(match_id: str) -> list[dict[str, str]]:
    """Stats del partido. **Solo viene poblado para partidos ya jugados.**"""
    text = _http_get(f"df_st_1_{match_id}")
    return parse_payload(text)


# ---------------------------------------------------------------------------
# Parser tipado de match statistics
# ---------------------------------------------------------------------------

# Mapeo nombre Flashscore (campo SG) -> clave canónica + parser de valor.
# El parser recibe el string crudo de SH/SI y devuelve un dict con los campos
# normalizados que se persisten en BD.
_PCT_DETAIL_RE = __import__("re").compile(r"^(\d+)%\s*\((\d+)/(\d+)\)$")


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip().replace(",", "")
    if value.isdigit():
        return int(value)
    if value.startswith("-") and value[1:].isdigit():
        return int(value)
    return None


def _parse_decimal(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.strip().replace(",", "."))
    except ValueError:
        return None


def _parse_percent(value: str | None) -> int | None:
    """Lee ``"67%"`` -> ``67``."""
    if not value:
        return None
    v = value.strip().rstrip("%")
    return _parse_int(v)


def _parse_pct_with_detail(value: str | None) -> tuple[int | None, int | None, int | None]:
    """Lee ``"91% (597/659)"`` -> ``(91, 597, 659)``.

    Devuelve tupla ``(pct, completed, attempted)``.
    """
    if not value:
        return (None, None, None)
    m = _PCT_DETAIL_RE.match(value.strip())
    if not m:
        # Fallback: solo porcentaje
        return (_parse_percent(value), None, None)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


# Stats que extraemos. Cada entrada: nombre flashscore -> ("kind", canonical_key)
# donde "kind" determina el parser:
#   "int"    -> _parse_int
#   "float"  -> _parse_decimal
#   "pct"    -> _parse_percent (solo "%")
#   "pct_d"  -> _parse_pct_with_detail (devuelve 3 valores con sufijos
#               "_pct", "_completed", "_attempted")
_STAT_MAP: dict[str, tuple[str, str]] = {
    "Expected goals (xG)": ("float", "xg"),
    "xG on target (xGOT)": ("float", "xg_on_target"),
    "Expected assists (xA)": ("float", "xa"),
    "Ball possession": ("pct", "possession_pct"),
    "Total shots": ("int", "shots_total"),
    "Shots on target": ("int", "shots_on_target"),
    "Shots off target": ("int", "shots_off_target"),
    "Blocked shots": ("int", "shots_blocked"),
    "Shots inside the box": ("int", "shots_inside_box"),
    "Shots outside the box": ("int", "shots_outside_box"),
    "Hit the woodwork": ("int", "shots_woodwork"),
    "Headed goals": ("int", "headed_goals"),
    "Big chances": ("int", "big_chances"),
    "Corner kicks": ("int", "corners"),
    "Offsides": ("int", "offsides"),
    "Free kicks": ("int", "free_kicks"),
    "Throw ins": ("int", "throw_ins"),
    "Touches in opposition box": ("int", "touches_in_box"),
    "Fouls": ("int", "fouls"),
    "Yellow cards": ("int", "yellow_cards"),
    "Red cards": ("int", "red_cards"),
    "Duels won": ("int", "duels_won"),
    "Clearances": ("int", "clearances"),
    "Interceptions": ("int", "interceptions"),
    "Saves": ("int", "saves"),
    "Goal kicks": ("int", "goal_kicks"),
    # Con detail (pct + made/attempted)
    "Passes": ("pct_d", "passes"),
    "Long passes": ("pct_d", "long_passes"),
    "Passes in final third": ("pct_d", "passes_final_third"),
    "Crosses": ("pct_d", "crosses"),
    "Tackles": ("pct_d", "tackles"),
}


def parse_match_statistics(records: list[dict[str, str]]) -> dict[str, dict[str, float | int]]:
    """Convierte los records crudos de ``df_st_1`` en stats canónicos.

    Devuelve un dict ``{"home": {...}, "away": {...}}`` con todas las claves
    de :data:`_STAT_MAP` que aparezcan en el feed. Las stats que aparecen
    repetidas (la misma stat puede listarse en varias secciones SF) se
    almacenan una sola vez (la primera ocurrencia gana, el resto debe ser
    idéntico salvo bugs de Flashscore).

    Para stats con detalle pct+raw (``passes``), añade tres claves:
    ``passes_pct``, ``passes_completed``, ``passes_attempted``.
    """
    home: dict[str, float | int] = {}
    away: dict[str, float | int] = {}

    for rec in records:
        name = rec.get("SG")
        if not name or name not in _STAT_MAP:
            continue
        kind, key = _STAT_MAP[name]
        sh, si = rec.get("SH"), rec.get("SI")

        if kind == "int":
            vh, va = _parse_int(sh), _parse_int(si)
            if vh is not None and key not in home:
                home[key] = vh
            if va is not None and key not in away:
                away[key] = va
        elif kind == "float":
            vh, va = _parse_decimal(sh), _parse_decimal(si)
            if vh is not None and key not in home:
                home[key] = vh
            if va is not None and key not in away:
                away[key] = va
        elif kind == "pct":
            vh, va = _parse_percent(sh), _parse_percent(si)
            if vh is not None and key not in home:
                home[key] = vh
            if va is not None and key not in away:
                away[key] = va
        elif kind == "pct_d":
            ph, ch, ah_ = _parse_pct_with_detail(sh)
            pa, ca, aa = _parse_pct_with_detail(si)
            for sub, val in (("_pct", ph), ("_completed", ch), ("_attempted", ah_)):
                k = f"{key}{sub}"
                if val is not None and k not in home:
                    home[k] = val
            for sub, val in (("_pct", pa), ("_completed", ca), ("_attempted", aa)):
                k = f"{key}{sub}"
                if val is not None and k not in away:
                    away[k] = val

    return {"home": home, "away": away}


def fetch_match_lineups(match_id: str) -> list[dict[str, str]]:
    """Alineaciones. **Disponible normalmente 1h antes del partido.**"""
    text = _http_get(f"df_lu_1_{match_id}")
    return parse_payload(text)


def _to_int_or_none(v: str | None) -> int | None:
    if v is None or v in {"", "-1"}:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


TOP5_LEAGUE_PREFIXES = (
    "SPAIN: LaLiga",
    "ENGLAND: Premier League",
    "GERMANY: Bundesliga",
    "ITALY: Serie A",
    "FRANCE: Ligue 1",
)


def _filter_top5(matches: Iterable[FlashscoreMatch]) -> list[FlashscoreMatch]:
    return [m for m in matches if m.league_full_name and any(
        m.league_full_name.startswith(prefix) for prefix in TOP5_LEAGUE_PREFIXES
    )]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cliente Flashscore (POC).")
    parser.add_argument("--fixtures-today", action="store_true",
                        help="Lista los partidos de hoy filtrados a las 5 grandes ligas.")
    parser.add_argument("--day-offset", type=int, default=0,
                        help="0=hoy, +1=mañana, -1=ayer (default 0).")
    parser.add_argument("--match-summary", default=None,
                        help="match_id de Flashscore para obtener su summary.")
    parser.add_argument("--save", action="store_true",
                        help="Guarda el resultado en data/raw/flashscore/ como JSON.")
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings: Settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    raw_dir = settings.raw_data_dir / "flashscore"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if args.fixtures_today:
        matches = fetch_fixtures_feed(day_offset=args.day_offset)
        top5 = _filter_top5(matches)
        print(f"Total partidos en feed: {len(matches)}")
        print(f"Top 5 ligas: {len(top5)}")
        print(f"{'liga':30s} {'home':22s} {'away':22s} {'kickoff (UTC)':16s}")
        print("-" * 95)
        for m in top5[:30]:
            ko = m.scheduled_at.strftime("%Y-%m-%d %H:%M") if m.scheduled_at else "-"
            print(f"{(m.league_full_name or '')[:29]:30s} "
                  f"{(m.home_team_name or '')[:21]:22s} "
                  f"{(m.away_team_name or '')[:21]:22s} "
                  f"{ko:16s}")
        if args.save:
            out = raw_dir / f"fixtures_d{args.day_offset:+d}_{timestamp}.json"
            _save(out, {"day_offset": args.day_offset, "matches": [m.to_dict() for m in matches]})
            print(f"\nGuardado en {out}")
        return 0

    if args.match_summary:
        summary = fetch_match_summary(args.match_summary)
        stats = fetch_match_statistics(args.match_summary)
        lineups = fetch_match_lineups(args.match_summary)
        print(f"Summary ({len(summary)} keys): {summary}")
        print(f"Stats ({len(stats)} records): {stats[:2]}...")
        print(f"Lineups ({len(lineups)} records): {lineups[:2]}...")
        if args.save:
            out = raw_dir / f"match_{args.match_summary}_{timestamp}.json"
            _save(out, {"match_id": args.match_summary, "summary": summary,
                        "statistics": stats, "lineups": lineups})
            print(f"\nGuardado en {out}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
