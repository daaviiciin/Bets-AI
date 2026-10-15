"""Emparejado de nombres de equipo y de jugador entre fuentes (Flashscore ↔ Sofascore ↔ Winamax).

Cada fuente escribe los clubes a su manera ("Bayern Munich" / "FC Bayern
München", "Atl. Madrid" / "Atlético Madrid"). Si al insertar un partido se
busca el equipo solo por nombre exacto, se crea un duplicado y sus stats
quedan repartidas entre dos filas de ``teams``.

Reglas (de más a menos estricta):

1. Se normaliza: sin tildes, minúsculas, sin puntuación, sin afijos de club
   ("FC", "1.", "04"…), con abreviaturas expandidas ("atl" → "atletico") y
   alias de nombre completo ("psg" → "paris saint germain").
2. Hay match si las palabras de un nombre están todas contenidas en el otro
   ("liverpool" ⊂ "liverpool fc", "union berlin" ⊂ "1. fc union berlin").
3. Nunca se empareja un filial/femenino/sub-N con el primer equipo.
4. El candidato tiene que ser **único**: si dos encajan, no se elige ninguno.
"""

from __future__ import annotations

import re
import unicodedata

# Afijos que no identifican al club.
_NOISE_TOKENS: frozenset[str] = frozenset({
    "fc", "cf", "ac", "as", "sc", "afc", "ssc", "rc", "rcd", "ud", "cd", "cp",
    "sv", "fsv", "vfb", "vfl", "tsg", "bv", "club", "de", "calcio", "ssd", "asd",
    "1", "04", "05", "07", "1907", "1909",
})

# Abreviaturas que usa Flashscore → palabra completa.
_TOKEN_ALIASES: dict[str, str] = {
    "atl": "atletico",
    "ath": "athletic",
    "dep": "deportivo",
    "utd": "united",
    "munich": "munchen",
    "cologne": "koln",
}

# Nombres que no se parecen en nada entre fuentes (ya normalizados).
_NAME_ALIASES: dict[str, str] = {
    "psg": "paris saint germain",
    "athletic bilbao": "athletic",
    "b monchengladbach": "borussia mgladbach",
    "racing santander": "real racing",
    "lyon": "olympique lyonnais",
    "brest": "stade brestois",
    "rennes": "stade rennais",
}

# Si uno de los nombres lleva alguno de estos y el otro no, son equipos distintos.
_SQUAD_MARKERS: frozenset[str] = frozenset({"b", "ii", "w", "women", "u18", "u19", "u21", "u23"})


def normalize_team_name(name: str | None) -> str:
    """Forma canónica para comparar nombres de equipo de distintas fuentes."""
    if not name:
        return ""
    txt = unicodedata.normalize("NFKD", name)
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    txt = re.sub(r"['’]", "", txt)  # M'gladbach → mgladbach
    txt = re.sub(r"[.&\-/()]", " ", txt)
    raw_tokens = txt.split()
    # Marcadores de filial/femenino antes de expandir alias ("b" ≠ "borussia").
    tokens = [_TOKEN_ALIASES.get(t, t) for t in raw_tokens]
    tokens = [t for t in tokens if t not in _NOISE_TOKENS]
    joined = " ".join(tokens)
    return _NAME_ALIASES.get(joined, joined)


def _squad_markers(name: str | None) -> frozenset[str]:
    if not name:
        return frozenset()
    txt = re.sub(r"['’]", "", name.lower())
    tokens = re.sub(r"[.&\-/()]", " ", txt).split()
    # "B." al inicio es abreviatura (B. Monchengladbach), no filial.
    if tokens and tokens[0] == "b":
        tokens = tokens[1:]
    return frozenset(t for t in tokens if t in _SQUAD_MARKERS)


def team_names_match(a: str | None, b: str | None) -> bool:
    """``True`` si ``a`` y ``b`` parecen el mismo club."""
    if _squad_markers(a) != _squad_markers(b):
        return False
    na, nb = normalize_team_name(a), normalize_team_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return ta <= tb or tb <= ta


def player_name_key(name: str | None) -> frozenset[str]:
    """Palabras del nombre sin tildes ni orden: "Gordon Anthony" == "Anthony Gordon".

    Flashscore escribe "Apellido Nombre" y Sofascore/Winamax "Nombre Apellido".
    """
    if not name:
        return frozenset()
    txt = unicodedata.normalize("NFKD", name)
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    txt = re.sub(r"['’]", "", txt)
    return frozenset(re.sub(r"[.\-]", " ", txt).split())


def _player_tokens_match(a: str, b: str) -> bool:
    """Misma palabra, o diminutivo/forma corta como prefijo ("nico" ~ "nicolas")."""
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def player_names_match(a: str | None, b: str | None) -> bool:
    """``True`` si dos nombres de jugador pueden ser la misma persona.

    Cada palabra del nombre más corto tiene que encajar con una palabra distinta del
    largo, en cualquier orden: "Nico Paz" ~ "Paz Nicolás", "Bremer" ~ "Gleison Bremer",
    "Oli McBurnie" ~ "McBurnie Oliver". Es permisiva a propósito: quien la use tiene que
    exigir candidato **único** dentro del equipo (hay "Gabriel Jesus" y "Gabriel Magalhães").
    """
    ka, kb = player_name_key(a), player_name_key(b)
    if not ka or not kb:
        return False
    short, long_ = sorted((sorted(ka), sorted(kb)), key=len)
    used: set[str] = set()
    for token in short:
        hit = next((t for t in long_ if t not in used and _player_tokens_match(token, t)), None)
        if hit is None:
            return False
        used.add(hit)
    return True


def find_matching_team_id(name: str, candidates: dict[int, str]) -> int | None:
    """Devuelve el id del **único** candidato que encaja con ``name``, o ``None``.

    Primero se busca igualdad tras normalizar; solo si no hay, inclusión de palabras.
    Así "PSG" elige "Paris Saint-Germain" aunque "Paris FC" ("paris") también encaje.
    """
    target = normalize_team_name(name)
    exact = [
        tid for tid, cand in candidates.items()
        if normalize_team_name(cand) == target and _squad_markers(cand) == _squad_markers(name)
    ]
    if exact:
        return exact[0] if len(exact) == 1 else None
    matches = [tid for tid, cand in candidates.items() if team_names_match(name, cand)]
    return matches[0] if len(matches) == 1 else None
