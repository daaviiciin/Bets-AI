"""Selección de los picks del día v2 (fase 3, bloque B10): simples 1,30–1,70 y combinada 3–6.

Funciones puras (sin BD) sobre líneas ya calificadas por el modelo. Reglas del usuario
(2026-10-21, banda de la combinada bajada a 3–6 el 2026-10-25):

- **Simples**: cuota entre ``odds_min`` y ``odds_max``; una por partido (la de mayor P del
  modelo) y ordenadas por P. Primero las que tienen valor (EV ≥ 0), hasta :data:`MAX_SIMPLES`;
  si quedan menos de :data:`MIN_SIMPLES` se completa con las mejores sin valor, marcadas
  ``sin_valor`` (y con aviso si el EV es muy negativo). La banda **no** se amplía: si no hay
  partidos suficientes en ella, salen menos picks y se explica en ``selection_meta``.
- **Combinada**: de 2 a 4 patas con cuota total entre ``combo_min`` y ``combo_max``. Primero se
  buscan patas de **partidos distintos**; solo si así no se llega a la banda, se admiten **varias
  selecciones de un mismo partido** (en Winamax, con MyMatch). Entre todas gana la mejor según
  :func:`puntuar_combinada`.

Selecciones de un mismo partido: no son independientes ("gana el local" y "más de 1,5 goles" van
juntas), así que su probabilidad conjunta sale de la matriz de marcadores del modelo (λ local y
visitante) y la cuota se estima corrigiendo el producto de cuotas con esa misma correlación. La
cuota real la pone Winamax en MyMatch; por eso también se guarda la cuota mínima con valor (1/P).

Ningún pick lleva importe: el modelo no sugiere cuánto apostar.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import combinations, product
from types import SimpleNamespace
from typing import Any

from backend.models.match import MATCH_STATUS_FINISHED
from backend.services import poisson_model as pm
from backend.services import value_calculator as vc
from backend.services.saved_parlay_grading import grade_single_leg

MIN_SIMPLES = 3
MAX_SIMPLES = 5
#: Con un EV por debajo, el pick sin valor lleva un aviso explícito.
EV_AVISO = Decimal("-0.08")
AVISO_CUOTA_CORTA = "El modelo ve la cuota corta"

# Patas candidatas de la combinada: de 1,25 a 2,00, en tres tramos. De cada partido entra la
# línea de mayor P de cada tramo; si entrasen sin más "las 3 de mayor P", serían siempre las
# más cortas (~1,30) y con 4 patas no se llegaría a la banda (1,35⁴ ≈ 3,3).
COMBO_TRAMOS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("1.25"), Decimal("1.45")),
    (Decimal("1.45"), Decimal("1.70")),
    (Decimal("1.70"), Decimal("2.00")),
)
COMBO_PATA_MIN = COMBO_TRAMOS[0][0]
COMBO_PATA_MAX = COMBO_TRAMOS[-1][1]
COMBO_MIN_PATAS = 2
COMBO_MAX_PATAS = 4
COMBO_MAX_PARTIDOS = 12

#: Mercados que se pueden juntar dentro de un mismo partido: los que decide el marcador final,
#: porque su probabilidad conjunta sale de la matriz de marcadores.
MERCADOS_MISMO_PARTIDO: frozenset[str] = frozenset({
    "h2h",
    "double_chance",
    "totals",
    "btts",
    "team_home_totals_goals",
    "team_away_totals_goals",
})
MISMO_PARTIDO_MAX_PATAS = 3
#: Mejores mercados de cada partido (uno por mercado) y partidos que entran en la búsqueda.
MISMO_PARTIDO_LINEAS = 6
MISMO_PARTIDO_MAX_PARTIDOS = 4
#: Dos selecciones del mismo partido no se juntan si una casi implica la otra ("gana el local"
#: y "1X") ni si casi se excluyen: sus cuotas multiplicadas no significarían nada.
UMBRAL_IMPLICA = 0.97
UMBRAL_EXCLUYE = 0.01
#: Goles por equipo de la matriz de marcadores (0..10): con λ ≤ 3 cubre > 99,9 % de la masa.
TAMANO_REJILLA = 11

#: Mercados que el modelo actual (``legacy-v1``) sabe calcular. Los props de jugador quedan
#: fuera de los picks hasta que se validen (plan de la fase 3); tiros a puerta y goles por
#: mitad entran con el modelo v2.
MERCADOS_PICKS: frozenset[str] = frozenset({
    *MERCADOS_MISMO_PARTIDO,
    "match_highest_scoring_half",
})


@dataclass(frozen=True)
class Candidata:
    """Una línea de Winamax con P del modelo, lista para competir por ser pick."""

    match_id: int
    home_team: str
    away_team: str
    league: str
    commence_time: datetime
    market_key: str
    outcome_name: str
    outcome_point: Decimal | None
    offered_odds: Decimal
    p_model: Decimal
    #: Goles esperados del modelo; hacen falta para juntar selecciones del mismo partido.
    lambda_home: Decimal | None = None
    lambda_away: Decimal | None = None

    @property
    def ev(self) -> Decimal:
        return vc.expected_value(self.offered_odds, self.p_model)


@dataclass(frozen=True)
class PickSimple:
    candidata: Candidata
    sin_valor: bool
    aviso: str | None = None


@dataclass(frozen=True)
class Combinada:
    patas: tuple[Candidata, ...]
    #: P conjunta del modelo: producto entre partidos, matriz de marcadores dentro de un partido.
    probabilidad: Decimal
    #: Cuota de la combinada; con selecciones del mismo partido es una estimación (MyMatch).
    cuota_total: Decimal
    #: Producto simple de las cuotas de Winamax (el que daría una combinada normal).
    cuota_winamax: Decimal
    mismo_partido: bool = False

    @property
    def ev(self) -> Decimal:
        return self.cuota_total * self.probabilidad - Decimal("1")

    @property
    def sin_valor(self) -> bool:
        return self.ev < 0

    @property
    def cuota_minima_valor(self) -> Decimal:
        """Cuota a partir de la cual la combinada tiene valor para el modelo (1/P)."""
        return Decimal("1") / self.probabilidad


def _producto(valores: Iterable[Decimal]) -> Decimal:
    total = Decimal("1")
    for v in valores:
        total *= v
    return total


def combinada_independiente(patas: tuple[Candidata, ...]) -> Combinada:
    """Combinada de partidos distintos: probabilidades y cuotas multiplicadas."""
    cuota = _producto(p.offered_odds for p in patas)
    return Combinada(
        patas=patas,
        probabilidad=_producto(p.p_model for p in patas),
        cuota_total=cuota,
        cuota_winamax=cuota,
    )


def _orden_por_probabilidad(c: Candidata) -> tuple:
    """P descendente, EV descendente y el resto solo para que el orden sea determinista."""
    return (-c.p_model, -c.ev, c.match_id, c.market_key, c.outcome_name, c.outcome_point or 0)


def _en_banda(c: Candidata, minimo: Decimal, maximo: Decimal) -> bool:
    return minimo <= c.offered_odds <= maximo


def _mejor_por_partido(cands: Iterable[Candidata]) -> list[Candidata]:
    """La línea de mayor P de cada partido, en orden de P."""
    mejor: dict[int, Candidata] = {}
    for c in sorted(cands, key=_orden_por_probabilidad):
        mejor.setdefault(c.match_id, c)
    return sorted(mejor.values(), key=_orden_por_probabilidad)


def elegir_simples(
    cands: Iterable[Candidata],
    *,
    odds_min: Decimal,
    odds_max: Decimal,
    min_simples: int = MIN_SIMPLES,
    max_simples: int = MAX_SIMPLES,
) -> tuple[list[PickSimple], dict[str, Any]]:
    """Simples del día y los recuentos para ``selection_meta``."""
    en_banda = [c for c in cands if _en_banda(c, odds_min, odds_max)]
    por_partido = _mejor_por_partido(en_banda)
    con_valor = [c for c in por_partido if c.ev >= 0]
    sin_valor = [c for c in por_partido if c.ev < 0]

    picks = [PickSimple(c, sin_valor=False) for c in con_valor[:max_simples]]
    for c in sin_valor[: max(0, min_simples - len(picks))]:
        aviso = AVISO_CUOTA_CORTA if c.ev < EV_AVISO else None
        picks.append(PickSimple(c, sin_valor=True, aviso=aviso))

    meta: dict[str, Any] = {
        "lineas_en_banda": len(en_banda),
        "partidos_en_banda": len(por_partido),
        "partidos_con_valor": len(con_valor),
        "simples_sin_valor": sum(p.sin_valor for p in picks),
    }
    if len(picks) < min_simples:
        n = len(por_partido)
        meta["motivo_simples"] = (
            f"Solo {n} {'partido' if n == 1 else 'partidos'} con cuota entre "
            f"{_fmt(odds_min)} y {_fmt(odds_max)}"
        )
    return picks, meta


# ---------------------------------------------------------------------------
# Combinada de partidos distintos
# ---------------------------------------------------------------------------


def _tramo(c: Candidata) -> int | None:
    """Índice del tramo de :data:`COMBO_TRAMOS` de la cuota (el último incluye su máximo)."""
    for i, (lo, hi) in enumerate(COMBO_TRAMOS):
        ultimo = i == len(COMBO_TRAMOS) - 1
        if lo <= c.offered_odds < hi or (ultimo and c.offered_odds == hi):
            return i
    return None


def grupos_para_combinada(cands: Iterable[Candidata]) -> list[list[Candidata]]:
    """Patas posibles por partido (la de mayor P de cada tramo), de los mejores partidos."""
    mejor: dict[tuple[int, int], Candidata] = {}
    for c in sorted(cands, key=_orden_por_probabilidad):
        t = _tramo(c)
        if t is not None and vc.is_recommendable_odds(c.offered_odds):
            mejor.setdefault((c.match_id, t), c)
    por_partido: dict[int, list[Candidata]] = defaultdict(list)
    for c in sorted(mejor.values(), key=_orden_por_probabilidad):
        por_partido[c.match_id].append(c)
    grupos = sorted(por_partido.values(), key=lambda g: _orden_por_probabilidad(g[0]))
    return grupos[:COMBO_MAX_PARTIDOS]


def enumerar_combinadas(
    grupos: list[list[Candidata]],
    *,
    total_min: Decimal,
    total_max: Decimal,
    min_patas: int = COMBO_MIN_PATAS,
    max_patas: int = COMBO_MAX_PATAS,
) -> Iterator[Combinada]:
    """Todas las combinadas de partidos distintos con cuota total en ``[total_min, total_max]``."""
    for n in range(min_patas, max_patas + 1):
        for partidos in combinations(range(len(grupos)), n):
            for patas in product(*(grupos[i] for i in partidos)):
                total = _producto(p.offered_odds for p in patas)
                if total_min <= total <= total_max:
                    yield combinada_independiente(tuple(patas))


# ---------------------------------------------------------------------------
# Selecciones de un mismo partido
# ---------------------------------------------------------------------------


def rejilla_marcadores(
    lambda_home: float, lambda_away: float, *, size: int = TAMANO_REJILLA,
) -> tuple[float, ...]:
    """P de cada marcador (local, visitante) en 0..size−1, aplanada y normalizada a 1."""
    grid = pm._build_grid(lambda_home, lambda_away, size=size)  # noqa: SLF001
    plano = [p for fila in grid for p in fila]
    total = math.fsum(plano)
    return tuple(p / total for p in plano)


def mascara_de_pata(c: Candidata, *, size: int = TAMANO_REJILLA) -> tuple[bool, ...] | None:
    """En qué marcadores gana la pata, según la propia liquidación (``grade_single_leg``).

    ``None`` si el mercado no lo decide el marcador final o alguna celda no se liquida sola
    (p. ej. una línea entera, que puede ser nula).
    """
    if c.market_key not in MERCADOS_MISMO_PARTIDO:
        return None
    pata = {
        "match_id": c.match_id,
        "home_team": c.home_team,
        "away_team": c.away_team,
        "market_key": c.market_key,
        "outcome_name": c.outcome_name,
        "outcome_point": format(c.outcome_point, "f") if c.outcome_point is not None else None,
    }
    equipos = {"home_team": SimpleNamespace(name=c.home_team),
               "away_team": SimpleNamespace(name=c.away_team)}
    mascara: list[bool] = []
    for h in range(size):
        for a in range(size):
            partido = SimpleNamespace(
                id=c.match_id, status=MATCH_STATUS_FINISHED, home_score=h, away_score=a,
                commence_time=c.commence_time, **equipos,
            )
            res = grade_single_leg(pata, partido)["result"]
            if res not in {"hit", "miss"}:
                return None
            mascara.append(res == "hit")
    return tuple(mascara)


def _p_conjunta(rejilla: tuple[float, ...], mascaras: list[tuple[bool, ...]]) -> float:
    return math.fsum(p for p, *gana in zip(rejilla, *mascaras) if all(gana))


class _BloquesMismoPartido:
    """Cachés de matrices y máscaras para valorar selecciones de un mismo partido."""

    def __init__(self) -> None:
        self._rejillas: dict[int, tuple[float, ...] | None] = {}
        self._mascaras: dict[Candidata, tuple[bool, ...] | None] = {}

    def rejilla(self, c: Candidata) -> tuple[float, ...] | None:
        if c.match_id not in self._rejillas:
            ok = c.lambda_home is not None and c.lambda_away is not None
            self._rejillas[c.match_id] = (
                rejilla_marcadores(float(c.lambda_home), float(c.lambda_away)) if ok else None
            )
        return self._rejillas[c.match_id]

    def mascara(self, c: Candidata) -> tuple[bool, ...] | None:
        if c not in self._mascaras:
            self._mascaras[c] = mascara_de_pata(c)
        return self._mascaras[c]

    def bloque(self, patas: list[Candidata]) -> tuple[Decimal, Decimal] | None:
        """``(P conjunta, cuota estimada)`` de varias selecciones de un partido, o ``None``.

        La matriz de marcadores solo aporta la **correlación** ``C = P(todas) / ∏P_i``; la P
        conjunta es el producto de las P de cada pata (ya calibradas y mezcladas con el mercado)
        por ``C``, y la cuota estimada, el producto de cuotas dividido por ``C``. Si las
        selecciones van juntas, ``C > 1``: la P sube y la cuota baja, como en MyMatch.
        """
        if len({p.market_key for p in patas}) != len(patas):
            return None
        rejilla = self.rejilla(patas[0])
        mascaras = [self.mascara(p) for p in patas]
        if rejilla is None or any(m is None for m in mascaras):
            return None
        marginales = [_p_conjunta(rejilla, [m]) for m in mascaras]
        for (i, mi), (j, mj) in combinations(enumerate(mascaras), 2):
            pij = _p_conjunta(rejilla, [mi, mj])
            if pij < UMBRAL_EXCLUYE or pij >= UMBRAL_IMPLICA * min(marginales[i], marginales[j]):
                return None
        conjunta = _p_conjunta(rejilla, mascaras)
        if conjunta <= 0:
            return None
        correlacion = conjunta / math.prod(marginales)
        p_modelo = math.prod(float(p.p_model) for p in patas) * correlacion
        p_modelo = min(p_modelo, min(float(p.p_model) for p in patas))
        cuota = _producto(p.offered_odds for p in patas) / Decimal(str(round(correlacion, 9)))
        return Decimal(str(round(p_modelo, 9))), cuota


def enumerar_combinadas_mismo_partido(
    cands: Iterable[Candidata],
    *,
    total_min: Decimal,
    total_max: Decimal,
    min_patas: int = COMBO_MIN_PATAS,
    max_patas: int = COMBO_MAX_PATAS,
) -> Iterator[Combinada]:
    """Combinadas que admiten varias selecciones (de mercados distintos) de un mismo partido."""
    mejor: dict[tuple[int, str], Candidata] = {}
    for c in sorted(cands, key=_orden_por_probabilidad):
        if (
            c.market_key in MERCADOS_MISMO_PARTIDO
            and _en_banda(c, COMBO_PATA_MIN, COMBO_PATA_MAX)
            and vc.is_recommendable_odds(c.offered_odds)
        ):
            mejor.setdefault((c.match_id, c.market_key), c)
    por_partido: dict[int, list[Candidata]] = defaultdict(list)
    for c in sorted(mejor.values(), key=_orden_por_probabilidad):
        if len(por_partido[c.match_id]) < MISMO_PARTIDO_LINEAS:
            por_partido[c.match_id].append(c)
    grupos = sorted(por_partido.values(), key=lambda g: _orden_por_probabilidad(g[0]))
    patas_posibles = [c for g in grupos[:MISMO_PARTIDO_MAX_PARTIDOS] for c in g]

    bloques = _BloquesMismoPartido()
    for n in range(min_patas, max_patas + 1):
        for patas in combinations(patas_posibles, n):
            por_bloque: dict[int, list[Candidata]] = defaultdict(list)
            for p in patas:
                por_bloque[p.match_id].append(p)
            if any(len(b) > MISMO_PARTIDO_MAX_PATAS for b in por_bloque.values()):
                continue
            probabilidad, cuota, valido = Decimal("1"), Decimal("1"), True
            for b in por_bloque.values():
                if len(b) == 1:
                    probabilidad *= b[0].p_model
                    cuota *= b[0].offered_odds
                    continue
                valor = bloques.bloque(b)
                if valor is None:
                    valido = False
                    break
                probabilidad *= valor[0]
                cuota *= valor[1]
            if valido and total_min <= cuota <= total_max:
                yield Combinada(
                    patas=tuple(patas),
                    probabilidad=probabilidad,
                    cuota_total=cuota,
                    cuota_winamax=_producto(p.offered_odds for p in patas),
                    mismo_partido=any(len(b) > 1 for b in por_bloque.values()),
                )


# ---------------------------------------------------------------------------
# Elección
# ---------------------------------------------------------------------------


def puntuar_combinada(combinada: Combinada) -> tuple:
    """Clave de orden de una combinada: gana la que dé la tupla **mayor**.

    Con valor primero; después, partidos distintos antes que selecciones del mismo partido; luego
    la mayor probabilidad conjunta y, a igualdad, menos patas. Se maximiza la probabilidad y no el
    EV a propósito: con un modelo sin calibrar, la combinada de mayor EV es la que acumula sus
    mayores errores ("maldición del optimizador"); la de mayor P es más robusta. Dentro de la
    banda eso empuja la cuota hacia su mínimo (≈ 3), la cuota habitual del usuario.
    """
    return (
        not combinada.sin_valor,
        not combinada.mismo_partido,
        combinada.probabilidad,
        -len(combinada.patas),
    )


def _mejor(combos: Iterable[Combinada], puntuar: Callable[[Combinada], tuple]) -> Combinada | None:
    mejor: Combinada | None = None
    mejor_clave: tuple | None = None
    for combo in combos:
        clave = puntuar(combo)
        if mejor_clave is None or clave > mejor_clave:
            mejor, mejor_clave = combo, clave
    return mejor


def elegir_combinada(
    cands: Iterable[Candidata],
    *,
    total_min: Decimal,
    total_max: Decimal,
    puntuar: Callable[[Combinada], tuple] = puntuar_combinada,
    permitir_mismo_partido: bool = True,
) -> Combinada | None:
    """La mejor combinada de partidos distintos; si no hay ninguna en la banda, la mejor que
    admita varias selecciones de un mismo partido. ``None`` si tampoco."""
    cands = list(cands)
    mejor = _mejor(
        enumerar_combinadas(grupos_para_combinada(cands), total_min=total_min, total_max=total_max),
        puntuar,
    )
    if mejor is None and permitir_mismo_partido:
        mejor = _mejor(
            enumerar_combinadas_mismo_partido(cands, total_min=total_min, total_max=total_max),
            puntuar,
        )
    return mejor


# ---------------------------------------------------------------------------
# Serialización
# ---------------------------------------------------------------------------


def _fmt(d: Decimal) -> str:
    return format(d.quantize(Decimal("0.01")), "f").replace(".", ",")


def pata_a_dict(
    c: Candidata,
    *,
    model_version: str,
    odds_captured_at: datetime | None,
    sin_valor: bool,
    aviso: str | None = None,
) -> dict[str, Any]:
    """Pata congelada: compatible con ``grade_single_leg`` y con el boletín del frontend."""
    return {
        "match_id": c.match_id,
        "home_team": c.home_team,
        "away_team": c.away_team,
        "league": c.league,
        "commence_time": c.commence_time.isoformat(),
        "market_key": c.market_key,
        "outcome_name": c.outcome_name,
        "outcome_point": format(c.outcome_point, "f") if c.outcome_point is not None else None,
        "player_name": None,
        "offered_odds": format(c.offered_odds, "f"),
        "model_probability": format(c.p_model.quantize(Decimal("0.000001")), "f"),
        "model_ev": format(c.ev.quantize(Decimal("0.0001")), "f"),
        "model_version": model_version,
        "odds_captured_at": odds_captured_at.isoformat() if odds_captured_at else None,
        "sin_valor": sin_valor,
        "aviso": aviso,
    }


def combinada_a_dict(
    combo: Combinada, *, model_version: str, odds_captured_at: datetime | None,
) -> dict[str, Any]:
    return {
        "legs": [
            pata_a_dict(
                p, model_version=model_version, odds_captured_at=odds_captured_at,
                sin_valor=p.ev < 0,
            )
            for p in combo.patas
        ],
        "combined_odds": format(combo.cuota_total.quantize(Decimal("0.01")), "f"),
        "joint_probability": format(combo.probabilidad.quantize(Decimal("0.000001")), "f"),
        "ev": format(combo.ev.quantize(Decimal("0.0001")), "f"),
        "sin_valor": combo.sin_valor,
        "mismo_partido": combo.mismo_partido,
        "cuota_winamax": format(combo.cuota_winamax.quantize(Decimal("0.01")), "f"),
        "cuota_minima_valor": format(combo.cuota_minima_valor.quantize(Decimal("0.01")), "f"),
    }


def huella_picks(picks_json: list[dict[str, Any]], combo_json: dict[str, Any] | None) -> str:
    """SHA-256 del JSON canónico de simples + combinada (para comprobar que no se tocaron)."""
    canonico = json.dumps(
        {"simples": picks_json, "combinada": combo_json},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


__all__ = [
    "Candidata",
    "Combinada",
    "MERCADOS_MISMO_PARTIDO",
    "MERCADOS_PICKS",
    "PickSimple",
    "combinada_a_dict",
    "combinada_independiente",
    "elegir_combinada",
    "elegir_simples",
    "enumerar_combinadas",
    "enumerar_combinadas_mismo_partido",
    "grupos_para_combinada",
    "huella_picks",
    "mascara_de_pata",
    "pata_a_dict",
    "puntuar_combinada",
    "rejilla_marcadores",
]
