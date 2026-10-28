"""Escudos en alta calidad (Wikipedia) para los equipos.

Los escudos de Flashscore (``Team.logo_url``) miden 30×30 px: a 56 px en el marcador ya se
ven borrosos. Aquí se busca el escudo original de la ficha de Wikipedia del club (casi
siempre un SVG) y se guarda en ``Team.logo_hd_url``; la API sirve ese si existe.

- :func:`elegir_pagina`: lógica pura; de la respuesta de la búsqueda elige la página que es el
  club, o ``None`` si nada encaja. :func:`escudo_de_pagina` saca su escudo (``elegir_escudo``
  hace las dos cosas).
- Si la página del club no expone su imagen (le pasa al SK Brann), se prueba con el logo de su
  ficha de Wikidata (propiedad P154, un fichero de Commons): :func:`logo_de_wikidata` y
  :func:`url_commons`, también puras.
- :class:`BuscadorEscudosHD`: consulta una vez por equipo (los ya mirados quedan marcados en
  ``logo_hd_checked_at``, haya escudo o no) y guarda el resultado.

Se piden también imágenes no libres (``pilicense=any``): los escudos de muchos clubes solo
están en la Wikipedia inglesa, no en Commons.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Team

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_UPLOAD = "https://upload.wikimedia.org/wikipedia/commons/"
TIMEOUT = 15
PAUSA_SEGUNDOS = 1.5
# Wikimedia pide identificar el cliente con un contacto en el User-Agent; sin él limita (429)
# mucho antes.
USER_AGENT = "BetsAI/1.0 (https://github.com/daaviiciin/Bets-AI; proyecto educativo)"

# Nombres de Flashscore que no se parecen al de la página de Wikipedia (abreviados o en el
# idioma del club). Revisado a mano el 2026-10-28 con los 10 equipos que no salían.
# Clave = ``Team.name``; valor = lo que se busca y con lo que se compara el título.
NOMBRE_EN_WIKIPEDIA: dict[str, str] = {
    "H. Beer Sheva": "Hapoel Beer Sheva",
    "Din. Zagreb": "Dinamo Zagreb",
    "Univ. Craiova": "Universitatea Craiova",
    "St. Truiden": "Sint-Truiden",
    "Crvena zvezda": "Red Star Belgrade",
    "KuPS": "Kuopion Palloseura",
}

# La descripción corta de la página (la de Wikidata) dice qué es: así se descartan ciudades,
# estadios o la sección femenina que salen en la búsqueda con el mismo nombre.
_ES_CLUB = re.compile(r"\b(football|soccer) club\b|\bassociation football\b|\bfootball team\b", re.I)
_NO_ES_EL_CLUB = re.compile(r"\b(women|femenino|féminine|frauen|reserve|youth|under-\d+|b team)\b", re.I)
# Filial por el título: "Real Sociedad B", "Bayern Munich II".
_FILIAL = re.compile(r"\s(B|C|II)$")
# Para el título idéntico al nombre basta con que la descripción hable de un club o equipo:
# la Real Sociedad se describe como "Spanish sports club", sin la palabra "football".
_CLUB_O_EQUIPO = re.compile(r"\b(club|team)\b", re.I)
# Nombre de fichero de un escudo; descarta fotos de estadios o de plantillas.
_FICHERO_ESCUDO = re.compile(r"(logo|crest|badge|emblem|escudo|wappen|stemma|blason|\.svg$)", re.I)
# Letras que NFKD no descompone en letra + tilde: sin esto "Lillestrøm" quedaría "lillestrm".
_LETRAS = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "ß": "ss",
                         "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ı": "i"})


def _plano(texto: str) -> str:
    """Minúsculas sin tildes ni signos: ``"Lillestrøm SK"`` → ``"lillestrom sk"``."""
    sin_tildes = unicodedata.normalize("NFKD", texto.translate(_LETRAS)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", sin_tildes.lower()).strip()


def _comparten_palabra(nombre: str, titulo: str) -> bool:
    """Alguna palabra (de 3+ letras) en común, o una que empieza por la otra si tiene 4+
    ("ferencvaros" ~ "ferencvarosi", "nordsjaelland" ~ "nordsjaelland")."""
    palabras = {w for w in _plano(nombre).split() if len(w) > 2} or set(_plano(nombre).split())
    del_titulo = set(_plano(titulo).split())
    if palabras & del_titulo:
        return True
    return any(
        len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))
        for a in palabras
        for b in del_titulo
    )


def elegir_pagina(respuesta: dict, nombre: str) -> dict | None:
    """La página del club ``nombre`` en una respuesta de ``generator=search``, o ``None``.

    Primero la que se llama exactamente como el equipo (si su descripción habla de un club);
    si no, la PRIMERA (en el orden de la búsqueda) que es un club de fútbol por su
    descripción, no es una sección femenina ni un filial y comparte nombre. No se sigue
    bajando aunque esa página no tenga escudo: más abajo salen homónimos (buscar "Inter" da
    también un club de Haití) y un escudo equivocado es peor que el borroso de Flashscore.
    """
    paginas = sorted(
        (respuesta.get("query") or {}).get("pages", {}).values(),
        key=lambda p: p.get("index", 0),
    )
    for p in paginas:
        if _plano(p.get("title") or "") == _plano(nombre) and _CLUB_O_EQUIPO.search(p.get("description") or ""):
            return p
    for p in paginas:
        descripcion = p.get("description") or ""
        titulo = p.get("title") or ""
        if not _ES_CLUB.search(descripcion) or _NO_ES_EL_CLUB.search(f"{titulo} {descripcion}"):
            continue
        if _FILIAL.search(titulo) or not _comparten_palabra(nombre, titulo):
            continue
        return p
    return None


def escudo_de_pagina(pagina: dict | None) -> str | None:
    """La imagen principal de la página si parece un escudo, sin el query ``?utm_source=...``."""
    url = (((pagina or {}).get("original") or {}).get("source") or "").split("?", 1)[0]
    return url if url and _FICHERO_ESCUDO.search(url.rsplit("/", 1)[-1]) else None


def elegir_escudo(respuesta: dict, nombre: str) -> str | None:
    """URL del escudo del club ``nombre`` en la respuesta de la búsqueda (o ``None``)."""
    return escudo_de_pagina(elegir_pagina(respuesta, nombre))


def logo_de_wikidata(respuesta: dict) -> str | None:
    """Nombre del fichero de Commons del logo (P154) en una respuesta de ``wbgetclaims``."""
    for claim in (respuesta.get("claims") or {}).get("P154", []):
        valor = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(valor, str) and valor:
            return valor
    return None


def url_commons(fichero: str) -> str:
    """URL directa de un fichero de Commons: la ruta sale del MD5 de su nombre con ``_``."""
    nombre = fichero.replace(" ", "_")
    h = hashlib.md5(nombre.encode("utf-8")).hexdigest()
    return f"{COMMONS_UPLOAD}{h[0]}/{h[:2]}/{quote(nombre)}"


class LimiteWikipedia(RuntimeError):
    """HTTP 429: Wikipedia pide frenar. Se corta la tanda; el resto, en el siguiente refresco."""


def _get(url: str, params: dict, que: str) -> dict:
    from curl_cffi import requests as cf

    r = cf.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if r.status_code == 429:
        raise LimiteWikipedia(f"HTTP 429 buscando {que!r}")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} buscando {que!r}")
    return r.json()


def _consultar(nombre: str) -> dict:
    return _get(
        WIKIPEDIA_API,
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{nombre} football club",
            "gsrlimit": 5,
            "prop": "pageimages|description|pageprops",
            "piprop": "original",
            "pilicense": "any",
            "ppprop": "wikibase_item",
        },
        nombre,
    )


def _consultar_wikidata(item: str) -> dict:
    return _get(
        WIKIDATA_API,
        {"action": "wbgetclaims", "format": "json", "entity": item, "property": "P154"},
        item,
    )


@dataclass
class EscudosHDResult:
    pendientes: int = 0
    encontrados: int = 0
    sin_escudo: int = 0
    fallidos: int = 0
    errores: list[str] = field(default_factory=list)


class BuscadorEscudosHD:
    """Busca el escudo HD de los equipos que aún no se han mirado."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        consultar=_consultar,
        consultar_wikidata=_consultar_wikidata,
        pausa: float = PAUSA_SEGUNDOS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._consultar = consultar
        self._consultar_wikidata = consultar_wikidata
        self._pausa = pausa

    def _buscar(self, nombre: str) -> str | None:
        pagina = elegir_pagina(self._consultar(nombre), nombre)
        url = escudo_de_pagina(pagina)
        item = ((pagina or {}).get("pageprops") or {}).get("wikibase_item")
        if url is None and item:
            fichero = logo_de_wikidata(self._consultar_wikidata(item))
            url = url_commons(fichero) if fichero else None
        return url

    def run(self, *, limit: int = 60) -> EscudosHDResult:
        result = EscudosHDResult()
        equipos = list(self.session.scalars(
            select(Team).where(Team.logo_hd_checked_at.is_(None)).order_by(Team.id).limit(limit)
        ).all())
        result.pendientes = len(equipos)
        for i, team in enumerate(equipos):
            if i and self._pausa:
                time.sleep(self._pausa)
            try:
                url = self._buscar(NOMBRE_EN_WIKIPEDIA.get(team.name, team.name))
            except LimiteWikipedia as exc:
                result.fallidos += 1
                result.errores.append(f"{team.name}: {exc} (se corta la tanda)")
                break
            except Exception as exc:  # noqa: BLE001
                # Sin marcar: se reintenta en el siguiente refresco.
                result.fallidos += 1
                result.errores.append(f"{team.name}: {exc}")
                continue
            team.logo_hd_url = url
            team.logo_hd_checked_at = datetime.now(timezone.utc)
            if url:
                result.encontrados += 1
            else:
                result.sin_escudo += 1
        self.session.commit()
        self.log.info(
            "Escudos HD: %d encontrados, %d sin escudo, %d fallidos (de %d pendientes).",
            result.encontrados, result.sin_escudo, result.fallidos, result.pendientes,
        )
        return result


__all__ = [
    "NOMBRE_EN_WIKIPEDIA",
    "BuscadorEscudosHD",
    "EscudosHDResult",
    "LimiteWikipedia",
    "elegir_escudo",
    "elegir_pagina",
    "escudo_de_pagina",
    "logo_de_wikidata",
    "url_commons",
]
