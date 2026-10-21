"""Escudos de los equipos (Flashscore) y sus colores dominantes.

- :func:`set_team_logo`: guarda en ``Team.logo_url`` el escudo que trae el calendario de
  Flashscore (campo ``OA``/``OB``: un nombre de fichero como ``EBfZuwme-bRmKmISE.png``).
- :func:`crest_colors`: lógica pura; de los bytes del PNG saca el color principal y uno
  secundario bien distinto (para pintar el marcador y las barras de cada equipo).
- :class:`TeamCrestColorizer`: descarga una vez el escudo de los equipos que aún no tienen
  color y lo guarda. ~150 imágenes en total; luego solo los equipos nuevos.
"""

from __future__ import annotations

import io
import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Team

FLASHSCORE_IMAGE_BASE = "https://static.flashscore.com/res/image/data/"
IMAGE_TIMEOUT = 15
DOWNLOAD_SLEEP_SECONDS = 0.3

# Cubos de color de 24 niveles por canal: agrupa tonos casi iguales del antialiasing.
_BUCKET = 24
# Distancia RGB mínima para que el secundario no sea un matiz del principal.
_MIN_SECONDARY_DISTANCE = 90.0

# Clubes cuyo escudo no da el color de su camiseta (revisado a mano el 2026-10-16 sobre los
# 96 equipos de las 5 ligas): el dorado de la senyera no es el Barça, el gris del contorno no
# es el Newcastle. Ganan al cálculo automático. Nombre = ``Team.name`` (el de Flashscore).
CLUB_COLOR_OVERRIDES: dict[str, tuple[str, str]] = {
    "FC Barcelona": ("#a50044", "#004d98"),
    "Real Madrid": ("#ffffff", "#febe10"),
    "Atlético Madrid": ("#cb3524", "#ffffff"),
    "Real Betis": ("#00954c", "#ffffff"),
    "Málaga CF": ("#0081c6", "#ffffff"),
    "Osasuna": ("#d91a21", "#0a346f"),
    "Rayo Vallecano": ("#ffffff", "#e53027"),
    "Elche": ("#05642c", "#ffffff"),
    "Deportivo de A Coruña": ("#005ca9", "#ffffff"),
    "Celta Vigo": ("#8ac3ee", "#e5254e"),
    "Espanyol": ("#007fc8", "#ffffff"),
    "Villarreal": ("#ffe667", "#005187"),
    "Valencia": ("#ffffff", "#ee3524"),
    "Manchester United": ("#da291c", "#fbe122"),
    "Manchester City": ("#6cabdd", "#ffffff"),
    "Newcastle United": ("#241f20", "#ffffff"),
    "Aston Villa": ("#670e36", "#95bfe5"),
    "Sunderland": ("#eb172b", "#ffffff"),
    "Coventry City": ("#59cbe8", "#ffffff"),
    "AC Milan": ("#fb090b", "#000000"),
    "AS Roma": ("#8e1f2f", "#f0bc42"),
    "Lazio": ("#87d8f7", "#ffffff"),
    "Udinese": ("#000000", "#ffffff"),
    "Lecce": ("#ffe100", "#d71a28"),
    "Juventus": ("#000000", "#ffffff"),
    "AS Monaco": ("#e51b22", "#ffffff"),
    "Nice": ("#c8102e", "#000000"),
    "Angers": ("#000000", "#ffffff"),
    "Borussia M'gladbach": ("#ffffff", "#000000"),
    "1. FC Union Berlin": ("#eb1923", "#ffffff"),
}


def flashscore_logo_url(code: str | None) -> str | None:
    """``"EBfZuwme-bRmKmISE.png"`` → URL completa; vacío o sin extensión → ``None``."""
    code = (code or "").strip()
    if not code or "." not in code or "/" in code:
        return None
    return FLASHSCORE_IMAGE_BASE + code


def set_team_logo(team: Team, code: str | None) -> bool:
    """Actualiza el escudo del equipo; si cambia, borra los colores para recalcularlos."""
    url = flashscore_logo_url(code)
    if url is None or team.logo_url == url:
        return False
    team.logo_url = url
    team.primary_color = None
    team.secondary_color = None
    return True


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _saturation(rgb: tuple[int, int, int]) -> float:
    hi, lo = max(rgb), min(rgb)
    return 0.0 if hi == 0 else (hi - lo) / hi


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def crest_colors(image_bytes: bytes) -> tuple[str, str | None]:
    """Color principal y secundario (``#rrggbb``) de un escudo.

    Ignora lo transparente y el blanco casi puro (fondo y ribetes). Entre los colores que
    quedan pesa el área y, sobre todo, la saturación: el rojo de una franja gana al gris de
    un contorno. Un escudo en blanco y negro devuelve negro (y blanco de secundario).
    """
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        rgba = img.convert("RGBA").resize((64, 64))
        pixels = rgba.get_flattened_data()  # Pillow ≥ 12.1 (sustituye a getdata)

    counts: Counter[tuple[int, int, int]] = Counter()
    sums: dict[tuple[int, int, int], list[int]] = {}
    white = 0
    for r, g, b, a in pixels:
        if a < 128:
            continue
        if min(r, g, b) > 235:
            white += 1
            continue
        key = (r // _BUCKET, g // _BUCKET, b // _BUCKET)
        counts[key] += 1
        acc = sums.setdefault(key, [0, 0, 0])
        acc[0] += r
        acc[1] += g
        acc[2] += b

    if not counts:
        return "#ffffff", None

    def mean(key: tuple[int, int, int]) -> tuple[int, int, int]:
        n = counts[key]
        r, g, b = sums[key]
        return (round(r / n), round(g / n), round(b / n))

    ranked = sorted(
        counts,
        key=lambda k: counts[k] * (0.25 + _saturation(mean(k))),
        reverse=True,
    )
    primary = mean(ranked[0])
    secondary = next(
        (mean(k) for k in ranked[1:]
         if counts[k] >= 0.04 * len(pixels) and _distance(mean(k), primary) >= _MIN_SECONDARY_DISTANCE),
        None,
    )
    if secondary is None and white >= 0.04 * len(pixels):
        secondary = (255, 255, 255)
    return _hex(primary), (_hex(secondary) if secondary else None)


def _download(url: str) -> bytes:
    from curl_cffi import requests as cf

    r = cf.get(url, impersonate="chrome", timeout=IMAGE_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} en {url}")
    return r.content


@dataclass
class CrestColorResult:
    pending: int = 0
    colored: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class TeamCrestColorizer:
    """Calcula y guarda los colores de los equipos que tienen escudo y aún no color."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch_image=_download,
        sleep_seconds: float = DOWNLOAD_SLEEP_SECONDS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch_image = fetch_image
        self._sleep_seconds = sleep_seconds

    def run(self, *, limit: int = 60) -> CrestColorResult:
        result = CrestColorResult()
        for team in self.session.scalars(
            select(Team).where(Team.name.in_(CLUB_COLOR_OVERRIDES))
        ).all():
            team.primary_color, team.secondary_color = CLUB_COLOR_OVERRIDES[team.name]
        teams = list(self.session.scalars(
            select(Team)
            .where(Team.logo_url.is_not(None))
            .where(Team.primary_color.is_(None))
            .order_by(Team.id)
            .limit(limit)
        ).all())
        result.pending = len(teams)
        for i, team in enumerate(teams):
            if i and self._sleep_seconds:
                time.sleep(self._sleep_seconds)
            try:
                team.primary_color, team.secondary_color = crest_colors(
                    self._fetch_image(team.logo_url),
                )
                result.colored += 1
            except Exception as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append(f"{team.name}: {exc}")
        self.session.commit()
        self.log.info(
            "Escudos: %d equipos coloreados, %d fallidos (de %d pendientes).",
            result.colored, result.failed, result.pending,
        )
        return result


__all__ = [
    "CLUB_COLOR_OVERRIDES",
    "CrestColorResult",
    "TeamCrestColorizer",
    "crest_colors",
    "flashscore_logo_url",
    "set_team_logo",
]
