"""Selección de los picks del día (fase 3, bloque B10): simples 1,30–2,00 y combinada 4–6.

Funciones puras (sin BD) sobre líneas ya calificadas por el modelo. Reglas ``v5`` (decisión del
usuario del 2026-11-17: **variedad** de mercados en lugar del perfil del 05/11, que obligaba a
llevar una doble oportunidad y limitaba las patas de goles):

- **Mercados**: 1X2, doble oportunidad, goles (del partido y de cada equipo, ambos marcan), mitad
  con más goles y, con el modelo v2, props de jugador de las familias que habilita el backtest
  de props (hoy goleador y tiros a puerta), marcados ``experimental``. Grupos de mercado:
  :data:`GRUPO_DE_MERCADO`. Nunca "menos goles" ni "ambos marcan: No" (:func:`es_menos_goles`).
- **Simples**: cuota entre ``odds_min`` y ``odds_max`` (1,30–2,00); una por partido (la de mayor
  P del modelo) y ordenadas por P, con como mucho :data:`MAX_SIMPLES_POR_GRUPO` del mismo grupo
  de mercado para que no salgan todas del mismo. Primero las que tienen valor (EV ≥ 0), hasta
  :data:`MAX_SIMPLES`; si quedan menos de :data:`MIN_SIMPLES` se completa con las mejores sin
  valor, marcadas ``sin_valor`` (y con aviso si el EV es muy negativo). La banda **no** se
  amplía: si no hay partidos suficientes en ella, salen menos picks y se explica en
  ``selection_meta``.
- **Combinada del día**: de 3 a 5 patas de 1,18 a 1,80 con cuota total entre ``combo_min`` y
  ``combo_max`` y **como mucho :data:`COMBO_MAX_PATAS_POR_GRUPO` patas del mismo grupo**.
  Primero se buscan patas de **partidos distintos**; solo si así no se llega a la banda, se
  admiten **varias selecciones de un mismo partido** (en Winamax, con MyMatch). Gana la mejor
  según :func:`puntuar_combinada`: con valor → partidos distintos → P conjunta redondeada a 1
  punto → más grupos distintos (las casi empatadas se deciden por variedad) → menos patas.
- **Combinada del partido** (B14, pick aparte): 2–3 selecciones de un mismo partido con cuota
  estimada 2,5–5, que puede llevar props.

Selecciones de un mismo partido: no son independientes ("gana el local" y "más de 1,5 goles" van
juntas), así que su probabilidad conjunta sale de la matriz de marcadores del modelo (λ local y
visitante) y la cuota se estima corrigiendo el producto de cuotas con esa misma correlación. La
cuota real la pone Winamax en MyMatch; por eso también se guarda la cuota mínima con valor (1/P).
Solo se juntan mercados que la matriz sabe valorar (:func:`pesos_de_pata`): los del marcador
final y los props con su P condicionada al marcador. La mitad con más goles no (depende de los
goles de cada mitad), así que solo va como simple o como pata de otro partido.

Ningún pick lleva importe: el modelo no sugiere cuánto apostar.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import total_ordering
from itertools import combinations
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

# Patas candidatas de la combinada: de 1,18 a 1,80 (reglas v5), en dos tramos de cuota. Por
# partido entran, en cada tramo, las :data:`COMBO_PATAS_POR_TRAMO` de mayor P de grupos de mercado
# distintos: la corta deja llegar a la banda con 4–5 patas y la larga, con 3 (1,59³ ≈ 4).
COMBO_PATA_MIN = Decimal("1.18")
#: Corte entre el tramo corto (1,18–1,45) y el largo (1,45–1,80): ~ media geométrica de la banda.
COMBO_PATA_CORTE = Decimal("1.45")
COMBO_PATA_MAX = Decimal("1.80")
COMBO_MIN_PATAS = 3
COMBO_MAX_PATAS = 5
#: Patas por partido y tramo: como mucho 6 por partido (2 tramos × 3), las mismas que con las
#: reglas v4 (3 grupos × 2 tramos), aunque ahora compitan 6 grupos. Con 8 partidos y la poda de
#: :func:`enumerar_combinadas` el peor caso ronda 1 s; con 4 por tramo serían 8 por partido y
#: unas 4 veces más combinaciones.
COMBO_PATAS_POR_TRAMO = 3
COMBO_MAX_PARTIDOS = 8
#: Variedad (reglas v5): como mucho estas patas del mismo grupo de mercado en la combinada.
COMBO_MAX_PATAS_POR_GRUPO = 2
#: Las combinadas con la misma P conjunta a esta precisión (1 punto) se deciden por variedad.
PRECISION_P_COMBINADA = Decimal("0.01")
#: Como mucho estas simples del mismo grupo de mercado.
MAX_SIMPLES_POR_GRUPO = 2

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

#: Mercados de equipo que pueden ser pick (los props de jugador van aparte, con el modelo v2).
#: La mitad con más goles salió el 2026-11-05 y volvió con las reglas v5 (2026-11-17).
MERCADOS_PICKS: frozenset[str] = MERCADOS_MISMO_PARTIDO | {"match_highest_scoring_half"}

#: Grupo de cada mercado de los picks: limita las simples y las patas repetidas de la combinada
#: (variedad, reglas v5) y desempata las combinadas a favor de la más variada.
GRUPO_DE_MERCADO: dict[str, str] = {
    "h2h": "1x2",
    "double_chance": "doble",
    "totals": "goles",
    "team_home_totals_goals": "goles",
    "team_away_totals_goals": "goles",
    "btts": "goles",
    "match_highest_scoring_half": "mitad",
    "player_sot": "tiros",
    "player_sot_alt": "tiros",
    "player_anytime_scorer": "goleador",
    "player_to_score_2plus": "goleador",
    # Hoy no la habilita el backtest de props; si entrara, sería un grupo más.
    "player_saves": "paradas",
    "player_saves_alt": "paradas",
}


def grupo_de_mercado(market_key: str) -> str:
    return GRUPO_DE_MERCADO.get(market_key, market_key)

#: Mercados de goles con lado "más/menos". Preferencia del usuario (2026-11-05, a partir de sus
#: boletos ganados): nunca apuesta a **menos** goles ni a "ambos marcan: No", solo a que haya
#: goles; esos lados no son pick.
MERCADOS_GOLES_MAS_MENOS: frozenset[str] = frozenset({
    "totals",
    "team_home_totals_goals",
    "team_away_totals_goals",
})


def es_menos_goles(market_key: str, outcome_name: str) -> bool:
    """``True`` si la selección apuesta a pocos goles: "menos de X" (del partido o de un
    equipo) o "ambos marcan: No"."""
    n = (outcome_name or "").strip().lower()
    if market_key == "btts":
        return n == "no"
    if market_key not in MERCADOS_GOLES_MAS_MENOS:
        return False
    return n.startswith(("under", "menos"))


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
    #: Props (experimentales; simples y combinadas desde las reglas v5): jugador y P
    #: condicionada a los goles de un lado (``("home"|"away", P(gana | k goles) para
    #: k = 0..10)``, B14d).
    player_name: str | None = None
    prop_condicional: tuple[str, tuple[float, ...]] | None = None

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


def orden_de_simples(c: Candidata) -> tuple:
    """Clave de orden de las simples: decide qué línea sale de cada partido y qué partidos van
    primero (las que tienen valor siguen yendo antes que las que no, en :func:`elegir_simples`).

    Con «mayor P» (el comportamiento hasta ahora) dentro de la banda 1,30–2,00 casi siempre gana
    la cuota más corta (~1,30, P ~75 %): por eso ampliar la banda no dio más variedad en la vista
    previa del 21/11. A mano: ``c.p_model``, ``c.ev`` (cuota × P − 1), ``c.offered_odds``,
    ``grupo_de_mercado(c.market_key)`` y ``es_prop(c)``. Más pequeño = va antes. Termina siempre
    con algo determinista (p. ej. ``*_orden_por_probabilidad(c)``).

    **Decidido (2026-11-18):** al usuario le es indiferente mientras salgan picks, así que se
    queda «mayor P», el más prudente con un modelo aún no calibrado del todo. La variedad la
    dan los topes por grupo de :func:`elegir_simples`, no el orden.
    """
    return _orden_por_probabilidad(c)


def clave_seleccion(c: Candidata) -> tuple:
    """Identifica una selección (partido, mercado, resultado y línea), sin la cuota ni la P."""
    return (c.match_id, c.market_key, c.outcome_name, c.outcome_point)


def sin_selecciones(cands: Iterable[Candidata], usadas: Iterable[Candidata]) -> list[Candidata]:
    """Las candidatas que no repiten ninguna selección de ``usadas``.

    Regla del usuario (2026-11-05): una selección que ya es pick simple no puede ir también en la
    combinada; sí puede ir otro mercado del mismo partido.
    """
    claves = {clave_seleccion(u) for u in usadas}
    return [c for c in cands if clave_seleccion(c) not in claves]


def _en_banda(c: Candidata, minimo: Decimal, maximo: Decimal) -> bool:
    return minimo <= c.offered_odds <= maximo


def _mejor_por_partido(
    cands: Iterable[Candidata], *, max_por_grupo: int | None = None,
) -> list[Candidata]:
    """La línea de mayor P de cada partido, en orden de P.

    Con ``max_por_grupo``, cuando un grupo de mercado ya tiene ese número de partidos, del
    siguiente partido se toma su mejor línea de otro grupo (o se salta si no tiene).
    """
    por_partido: dict[int, list[Candidata]] = defaultdict(list)
    for c in sorted(cands, key=orden_de_simples):
        por_partido[c.match_id].append(c)
    partidos = sorted(por_partido.values(), key=lambda g: orden_de_simples(g[0]))
    elegidas: list[Candidata] = []
    usados: dict[str, int] = defaultdict(int)
    for lineas in partidos:
        for c in lineas:
            g = grupo_de_mercado(c.market_key)
            if max_por_grupo is None or usados[g] < max_por_grupo:
                elegidas.append(c)
                usados[g] += 1
                break
    return sorted(elegidas, key=orden_de_simples)


def elegir_simples(
    cands: Iterable[Candidata],
    *,
    odds_min: Decimal,
    odds_max: Decimal,
    min_simples: int = MIN_SIMPLES,
    max_simples: int = MAX_SIMPLES,
    max_por_grupo: int | None = MAX_SIMPLES_POR_GRUPO,
) -> tuple[list[PickSimple], dict[str, Any]]:
    """Simples del día y los recuentos para ``selection_meta``."""
    en_banda = [c for c in cands if _en_banda(c, odds_min, odds_max)]
    n_partidos = len({c.match_id for c in en_banda})
    por_partido = _mejor_por_partido(en_banda, max_por_grupo=max_por_grupo)
    con_valor = [c for c in por_partido if c.ev >= 0]
    sin_valor = [c for c in por_partido if c.ev < 0]

    picks = [PickSimple(c, sin_valor=False) for c in con_valor[:max_simples]]
    for c in sin_valor[: max(0, min_simples - len(picks))]:
        aviso = AVISO_CUOTA_CORTA if c.ev < EV_AVISO else None
        picks.append(PickSimple(c, sin_valor=True, aviso=aviso))

    meta: dict[str, Any] = {
        "lineas_en_banda": len(en_banda),
        "partidos_en_banda": n_partidos,
        "partidos_con_valor": len(con_valor),
        "simples_sin_valor": sum(p.sin_valor for p in picks),
    }
    if len(picks) < min_simples:
        meta["motivo_simples"] = (
            f"Solo {n_partidos} {'partido' if n_partidos == 1 else 'partidos'} con cuota entre "
            f"{_fmt(odds_min)} y {_fmt(odds_max)}"
        )
    return picks, meta


# ---------------------------------------------------------------------------
# Combinada de partidos distintos
# ---------------------------------------------------------------------------


def _tramo_pata(c: Candidata) -> int:
    """0 si la pata es corta (por debajo de :data:`COMBO_PATA_CORTE`), 1 si es larga."""
    return 0 if c.offered_odds < COMBO_PATA_CORTE else 1


def grupos_para_combinada(cands: Iterable[Candidata]) -> list[list[Candidata]]:
    """Patas posibles por partido, de los mejores partidos: en cada tramo de cuota (1,18–1,45 y
    1,45–1,80), las :data:`COMBO_PATAS_POR_TRAMO` de mayor P de grupos de mercado distintos.

    Solo con la de mayor P entrarían siempre las más cortas (~1,27) y, con pocos partidos, la
    combinada no llegaría a la banda (1,27⁴ ≈ 2,6). El tope por tramo mantiene acotada la
    búsqueda (:func:`enumerar_combinadas`) aunque compitan los seis grupos.
    """
    por_tramo: dict[tuple[int, int], list[Candidata]] = defaultdict(list)
    for c in sorted(cands, key=_orden_por_probabilidad):
        if not (_en_banda(c, COMBO_PATA_MIN, COMBO_PATA_MAX) and vc.is_recommendable_odds(c.offered_odds)):
            continue
        tramo = por_tramo[(c.match_id, _tramo_pata(c))]
        grupo = grupo_de_mercado(c.market_key)
        if len(tramo) < COMBO_PATAS_POR_TRAMO and all(grupo_de_mercado(t.market_key) != grupo for t in tramo):
            tramo.append(c)
    por_partido: dict[int, list[Candidata]] = defaultdict(list)
    for c in sorted((c for t in por_tramo.values() for c in t), key=_orden_por_probabilidad):
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
    max_por_grupo: int = COMBO_MAX_PATAS_POR_GRUPO,
) -> Iterator[Combinada]:
    """Todas las combinadas de partidos distintos con cuota total en ``[total_min, total_max]`` y
    como mucho ``max_por_grupo`` patas de cada grupo de mercado.

    Búsqueda en profundidad (cada partido: se salta o aporta una pata) que corta las ramas que
    ya se pasan de ``total_max``, que ni con patas de la cuota más alta llegarían a ``total_min``
    o que repetirían un grupo más veces de las permitidas.
    """
    # La poda va en float (rápido) con un margen; la banda exacta se comprueba en Decimal.
    cuotas = [[float(c.offered_odds) for c in g] for g in grupos]
    nombres = [[grupo_de_mercado(c.market_key) for c in g] for g in grupos]
    tope = max((q for g in cuotas for q in g), default=1.0)
    lo, hi = float(total_min) * (1 - 1e-9), float(total_max) * (1 + 1e-9)
    usados: Counter[str] = Counter()

    def buscar(i: int, patas: tuple[Candidata, ...], total: float) -> Iterator[Combinada]:
        n = len(patas)
        if n >= min_patas and total >= lo:
            combo = combinada_independiente(patas)
            if total_min <= combo.cuota_total <= total_max:
                yield combo
        libres = min(max_patas - n, len(grupos) - i)
        if libres <= 0 or n + libres < min_patas or total * tope**libres < lo:
            return
        for j in range(i, len(grupos)):
            for c, q, g in zip(grupos[j], cuotas[j], nombres[j]):
                if total * q <= hi and usados[g] < max_por_grupo:
                    usados[g] += 1
                    yield from buscar(j + 1, (*patas, c), total * q)
                    usados[g] -= 1

    yield from buscar(0, (), 1.0)


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


def es_prop(c: Candidata) -> bool:
    """Prop de jugador con su P condicionada al marcador (solo con el modelo v2)."""
    return c.prop_condicional is not None


def pesos_de_pata(c: Candidata, *, size: int = TAMANO_REJILLA) -> tuple[float, ...] | None:
    """P de que gane la pata en cada marcador (local, visitante), aplanada como la rejilla.

    Mercados del marcador: 1 o 0 (:func:`mascara_de_pata`). Props: la P condicionada a los
    goles de su equipo o del rival (``prop_condicional``, B14d), p. ej. el goleador por
    *thinning*: ``1 − (1 − q)^k`` con k goles de su equipo. ``None`` si la matriz no sabe
    valorarla (la mitad con más goles, un prop sin vector): no se junta con otras del partido.
    """
    if c.prop_condicional is None:
        m = mascara_de_pata(c, size=size)
        return None if m is None else tuple(1.0 if x else 0.0 for x in m)
    lado, v = c.prop_condicional
    if len(v) < size or lado not in {"home", "away"}:
        return None
    return tuple(v[h] if lado == "home" else v[a] for h in range(size) for a in range(size))


def _p_ponderada(rejilla: tuple[float, ...], pesos: list[tuple[float, ...]]) -> float:
    return math.fsum(p * math.prod(ws) for p, *ws in zip(rejilla, *pesos))


def _patas_distintas(patas: Iterable[Candidata]) -> bool:
    """Sin repetir mercado (salvo props de jugadores distintos) ni jugador."""
    patas = list(patas)
    if len({(p.market_key, p.player_name if es_prop(p) else None) for p in patas}) != len(patas):
        return False
    jugadores = [p.player_name for p in patas if es_prop(p)]
    return len(set(jugadores)) == len(jugadores)


def _correlacion_bloque(
    rejilla: tuple[float, ...], pesos: list[tuple[float, ...]],
) -> float | None:
    """``C = P(todas) / ∏P_i`` de varias selecciones de un partido según la matriz, o ``None``
    si alguna no gana nunca, casi implica a otra o casi la excluye."""
    marginales = [_p_ponderada(rejilla, [w]) for w in pesos]
    if min(marginales) <= 0:
        return None
    for (i, wi), (j, wj) in combinations(enumerate(pesos), 2):
        pij = _p_ponderada(rejilla, [wi, wj])
        if pij < UMBRAL_EXCLUYE or pij >= UMBRAL_IMPLICA * min(marginales[i], marginales[j]):
            return None
    conjunta = _p_ponderada(rejilla, pesos)
    if conjunta <= 0:
        return None
    return conjunta / math.prod(marginales)


class _BloquesMismoPartido:
    """Cachés de matrices, pesos y bloques para valorar selecciones de un mismo partido."""

    def __init__(self) -> None:
        self._rejillas: dict[int, tuple[float, ...] | None] = {}
        self._pesos: dict[Candidata, tuple[float, ...] | None] = {}
        self._bloques: dict[tuple[Candidata, ...], tuple[Decimal, Decimal] | None] = {}

    def rejilla(self, c: Candidata) -> tuple[float, ...] | None:
        if c.match_id not in self._rejillas:
            ok = c.lambda_home is not None and c.lambda_away is not None
            self._rejillas[c.match_id] = (
                rejilla_marcadores(float(c.lambda_home), float(c.lambda_away)) if ok else None
            )
        return self._rejillas[c.match_id]

    def pesos(self, c: Candidata) -> tuple[float, ...] | None:
        if c not in self._pesos:
            self._pesos[c] = pesos_de_pata(c)
        return self._pesos[c]

    def bloque(self, patas: list[Candidata]) -> tuple[Decimal, Decimal] | None:
        """``(P conjunta, cuota estimada)`` de varias selecciones de un partido, o ``None``.

        La matriz de marcadores solo aporta la **correlación** ``C = P(todas) / ∏P_i``; la P
        conjunta es el producto de las P de cada pata (ya calibradas y mezcladas con el mercado)
        por ``C``, y la cuota estimada, el producto de cuotas dividido por ``C``. Si las
        selecciones van juntas, ``C > 1``: la P sube y la cuota baja, como en MyMatch. Un prop
        entra con su P condicionada al marcador (:func:`pesos_de_pata`); sin ella, no se junta.
        """
        clave = tuple(patas)
        if clave not in self._bloques:
            self._bloques[clave] = self._valorar(patas)
        return self._bloques[clave]

    def _valorar(self, patas: list[Candidata]) -> tuple[Decimal, Decimal] | None:
        if not _patas_distintas(patas):
            return None
        rejilla = self.rejilla(patas[0])
        pesos = [self.pesos(p) for p in patas]
        if rejilla is None or any(w is None for w in pesos):
            return None
        correlacion = _correlacion_bloque(rejilla, [w for w in pesos if w is not None])
        if correlacion is None:
            return None
        p_modelo = math.prod(float(p.p_model) for p in patas) * correlacion
        p_modelo = min(p_modelo, min(float(p.p_model) for p in patas))
        cuota = _producto(p.offered_odds for p in patas) / Decimal(str(round(correlacion, 9)))
        return Decimal(str(round(p_modelo, 9))), cuota


def _excede_grupos(patas: Iterable[Candidata], max_por_grupo: int) -> bool:
    return max(Counter(grupo_de_mercado(p.market_key) for p in patas).values(), default=0) > max_por_grupo


def enumerar_combinadas_mismo_partido(
    cands: Iterable[Candidata],
    *,
    total_min: Decimal,
    total_max: Decimal,
    min_patas: int = COMBO_MIN_PATAS,
    max_patas: int = COMBO_MAX_PATAS,
    max_por_grupo: int = COMBO_MAX_PATAS_POR_GRUPO,
) -> Iterator[Combinada]:
    """Combinadas que admiten varias selecciones (de mercados distintos) de un mismo partido.

    Entran todos los mercados, pero solo se juntan en un partido los que la matriz sabe valorar
    (:func:`pesos_de_pata`); el resto (la mitad con más goles) solo va como la única pata de su
    partido. Como en :func:`enumerar_combinadas`, como mucho ``max_por_grupo`` del mismo grupo.
    """
    mejor: dict[tuple[int, str], Candidata] = {}
    for c in sorted(cands, key=_orden_por_probabilidad):
        if _en_banda(c, COMBO_PATA_MIN, COMBO_PATA_MAX) and vc.is_recommendable_odds(c.offered_odds):
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
            if _excede_grupos(patas, max_por_grupo):
                continue
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


def grupos_distintos(combinada: Combinada) -> int:
    """Cuántos grupos de mercado distintos lleva la combinada (más = más variada)."""
    return len({grupo_de_mercado(p.market_key) for p in combinada.patas})


def _clave_determinista(combinada: Combinada) -> tuple[str, ...]:
    """Las selecciones de la combinada, ordenadas: solo para que el desempate no dependa del
    orden en que llegan las líneas."""
    return tuple(sorted(
        f"{p.match_id}|{p.market_key}|{p.outcome_name}|{p.outcome_point}|{p.player_name or ''}"
        for p in combinada.patas
    ))


@total_ordering
class _Desempate:
    """Último elemento de la clave de orden: :func:`_clave_determinista`, calculada solo si
    todo lo anterior empata (casi nunca). Con cientos de miles de combinadas en la banda,
    calcularla para todas doblaba el tiempo de :func:`elegir_combinada`."""

    __slots__ = ("_clave", "_combinada")

    def __init__(self, combinada: Combinada) -> None:
        self._combinada = combinada
        self._clave: tuple[str, ...] | None = None

    def clave(self) -> tuple[str, ...]:
        if self._clave is None:
            self._clave = _clave_determinista(self._combinada)
        return self._clave

    def __eq__(self, otro: object) -> bool:
        return isinstance(otro, _Desempate) and self.clave() == otro.clave()

    def __lt__(self, otro: _Desempate) -> bool:
        return self.clave() < otro.clave()

    __hash__ = None  # type: ignore[assignment]


def puntuar_combinada(combinada: Combinada) -> tuple:
    """Clave de orden de una combinada del día: gana la que dé la tupla **mayor**.

    Con valor → partidos distintos antes que selecciones del mismo partido → mayor probabilidad
    conjunta **redondeada a 1 punto** (:data:`PRECISION_P_COMBINADA`) → más grupos de mercado
    distintos (así, entre dos casi empatadas gana la más variada) → menos patas → la P exacta y
    las selecciones, para que el desempate sea determinista. Se maximiza la probabilidad y no el
    EV a propósito: con un modelo sin calibrar, la combinada de mayor EV es la que acumula sus
    mayores errores ("maldición del optimizador"); la de mayor P es más robusta.
    """
    return (
        not combinada.sin_valor,
        not combinada.mismo_partido,
        combinada.probabilidad.quantize(PRECISION_P_COMBINADA, rounding=ROUND_HALF_UP),
        grupos_distintos(combinada),
        -len(combinada.patas),
        combinada.probabilidad,
        _Desempate(combinada),
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
    """Pata congelada: compatible con ``grade_single_leg`` y con el boletín del frontend.

    Los props llevan el jugador (la liquidación lo busca en ``player_match_stats``) y
    ``experimental: true``: se miden aparte hasta tener picks reales (reglas v5).
    """
    prop = es_prop(c)
    return {
        "match_id": c.match_id,
        "home_team": c.home_team,
        "away_team": c.away_team,
        "league": c.league,
        "commence_time": c.commence_time.isoformat(),
        "market_key": c.market_key,
        "outcome_name": c.outcome_name,
        "outcome_point": format(c.outcome_point, "f") if c.outcome_point is not None else None,
        "player_name": c.player_name if prop else None,
        "offered_odds": format(c.offered_odds, "f"),
        "model_probability": format(c.p_model.quantize(Decimal("0.000001")), "f"),
        "model_ev": format(c.ev.quantize(Decimal("0.0001")), "f"),
        "model_version": model_version,
        "odds_captured_at": odds_captured_at.isoformat() if odds_captured_at else None,
        "sin_valor": sin_valor,
        "aviso": aviso,
        "experimental": prop,
    }


def combinada_a_dict(
    combo: Combinada, *, model_version: str, odds_captured_at: datetime | None,
) -> dict[str, Any]:
    """Combinada congelada; ``experimental`` si alguna pata es un prop."""
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
        "experimental": any(es_prop(p) for p in combo.patas),
    }


# ---------------------------------------------------------------------------
# Combinada del partido (B14d–B14e): 2–3 selecciones de un mismo partido, con props
# ---------------------------------------------------------------------------

#: Cuota total de la combinada del partido (decisión del usuario del 2026-10-27).
PARTIDO_TOTAL_MIN = Decimal("2.50")
PARTIDO_TOTAL_MAX = Decimal("5.00")
PARTIDO_MIN_PATAS = 2
PARTIDO_MAX_PATAS = 3
#: Cuota mínima de cada pata (como las patas de la combinada normal).
PARTIDO_PATA_MIN = COMBO_PATA_MIN
#: Por partido entran las mejores líneas del marcador (una por mercado) y los mejores props
#: (uno por jugador).
PARTIDO_LINEAS_MARCADOR = 6
PARTIDO_PROPS = 4
#: Preferencia del usuario entre props (2026-10-27): tiros a puerta > goleador > paradas.
PREFERENCIA_PROPS: dict[str, int] = {
    "player_sot": 0, "player_sot_alt": 0,
    "player_anytime_scorer": 1, "player_to_score_2plus": 1,
    "player_saves": 2, "player_saves_alt": 2,
}
MERCADOS_PROPS_PARTIDO: frozenset[str] = frozenset(PREFERENCIA_PROPS)


def combinada_del_partido(patas: tuple[Candidata, ...]) -> Combinada | None:
    """P conjunta y cuota estimada de 2–3 selecciones de un mismo partido (B14d).

    Igual que las selecciones del mismo partido de la combinada normal, pero con props: la
    matriz de marcadores da la **correlación** ``C = P(todas) / ∏P_i`` (los props, con su P
    condicionada al marcador; dado el marcador, se toman independientes). La P conjunta es
    ``∏ p_model · C`` y la cuota estimada ``∏ cuotas / C``. ``None`` si las patas no son de un
    mismo partido, repiten mercado o jugador, alguna casi implica o excluye a otra, o falta la
    matriz.
    """
    if len({p.match_id for p in patas}) != 1 or not _patas_distintas(patas):
        return None
    c0 = patas[0]
    if c0.lambda_home is None or c0.lambda_away is None:
        return None
    rejilla = rejilla_marcadores(float(c0.lambda_home), float(c0.lambda_away))
    pesos = [pesos_de_pata(p) for p in patas]
    if any(w is None for w in pesos):
        return None
    correlacion = _correlacion_bloque(rejilla, [w for w in pesos if w is not None])
    if correlacion is None:
        return None
    p_modelo = min(math.prod(float(p.p_model) for p in patas) * correlacion,
                   min(float(p.p_model) for p in patas))
    cuota_winamax = _producto(p.offered_odds for p in patas)
    return Combinada(
        patas=patas,
        probabilidad=Decimal(str(round(p_modelo, 9))),
        cuota_total=cuota_winamax / Decimal(str(round(correlacion, 9))),
        cuota_winamax=cuota_winamax,
        mismo_partido=True,
    )


def patas_para_combinada_del_partido(cands: Iterable[Candidata]) -> dict[int, list[Candidata]]:
    """Patas posibles de cada partido: las mejores del marcador (una por mercado, sin "menos
    goles" ni "ambos marcan: No") y los mejores props "más de" (uno por jugador, primero por la
    preferencia del usuario y luego por P). Todas con cuota recomendable."""
    marcador: dict[tuple[int, str], Candidata] = {}
    props: dict[tuple[int, str], Candidata] = {}
    for c in sorted(cands, key=_orden_por_probabilidad):
        if c.offered_odds < PARTIDO_PATA_MIN or not vc.is_recommendable_odds(c.offered_odds):
            continue
        if es_prop(c):
            if c.market_key in MERCADOS_PROPS_PARTIDO and c.player_name:
                props.setdefault((c.match_id, c.player_name), c)
        elif c.market_key in MERCADOS_MISMO_PARTIDO and not es_menos_goles(c.market_key, c.outcome_name):
            marcador.setdefault((c.match_id, c.market_key), c)
    por_partido: dict[int, list[Candidata]] = defaultdict(list)
    for c in sorted(marcador.values(), key=_orden_por_probabilidad):
        if len(por_partido[c.match_id]) < PARTIDO_LINEAS_MARCADOR:
            por_partido[c.match_id].append(c)
    n_props: dict[int, int] = defaultdict(int)
    orden_props = sorted(props.values(), key=lambda c: (PREFERENCIA_PROPS.get(c.market_key, 9),
                                                        *_orden_por_probabilidad(c)))
    for c in orden_props:
        if n_props[c.match_id] < PARTIDO_PROPS:
            por_partido[c.match_id].append(c)
            n_props[c.match_id] += 1
    return dict(sorted(por_partido.items()))


def enumerar_combinadas_del_partido(
    cands: Iterable[Candidata],
    *,
    total_min: Decimal = PARTIDO_TOTAL_MIN,
    total_max: Decimal = PARTIDO_TOTAL_MAX,
) -> Iterator[Combinada]:
    """Todas las combinadas de 2–3 patas de un mismo partido con cuota estimada en la banda."""
    for patas in patas_para_combinada_del_partido(cands).values():
        for n in range(PARTIDO_MIN_PATAS, PARTIDO_MAX_PATAS + 1):
            for grupo in combinations(patas, n):
                combo = combinada_del_partido(grupo)
                if combo is not None and total_min <= combo.cuota_total <= total_max:
                    yield combo


def puntuar_combinada_del_partido(combinada: Combinada) -> tuple:
    """Clave de orden de las combinadas del partido candidatas: gana la tupla **mayor**.

    Criterios posibles (a decidir por el usuario):

    - **EV conjunto** (``combinada.ev`` = cuota estimada × P conjunta − 1): premia el valor,
      pero es el que más acumula los errores del modelo ("maldición del optimizador").
    - **P conjunta** (``combinada.probabilidad``): más robusta; tiende a las cuotas bajas de
      la banda (2,5).
    - **Con valor o sin valor** (``combinada.sin_valor``): primero las de EV ≥ 0.
    - **Número de patas** (``len(combinada.patas)``): menos patas, menos correlación estimada
      y menos dependencia de la cuota de MyMatch.
    - **Si lleva prop** (:func:`es_prop` en alguna pata): son experimentales; se pueden preferir
      (mercado que el usuario cree explotable) o penalizar (aún sin forward test).
    - **Preferencia entre props** (:data:`PREFERENCIA_PROPS`: tiros a puerta > goleador >
      paradas).

    Cualquier criterio debería cumplir: con la misma cuota, más P no puntúa menos; y la clave
    es determinista (misma combinada, misma clave). Desde las reglas v5, a igualdad de todo lo
    anterior gana la más variada (más grupos de mercado) y, al final, un desempate determinista,
    como en :func:`puntuar_combinada`.
    """
    # TODO(usuario): elige y ordena los criterios. Provisional: mayor EV conjunto y, a igualdad,
    # mayor P conjunta, menos patas y más variada.
    return (
        combinada.ev,
        combinada.probabilidad,
        -len(combinada.patas),
        grupos_distintos(combinada),
        _Desempate(combinada),
    )


def elegir_combinada_del_partido(
    cands: Iterable[Candidata],
    *,
    total_min: Decimal = PARTIDO_TOTAL_MIN,
    total_max: Decimal = PARTIDO_TOTAL_MAX,
    puntuar: Callable[[Combinada], tuple] = puntuar_combinada_del_partido,
) -> Combinada | None:
    """La mejor combinada del partido (``None`` si ninguna llega a la banda). No toca las
    simples ni la combinada normal: es un pick aparte."""
    return _mejor(enumerar_combinadas_del_partido(cands, total_min=total_min, total_max=total_max), puntuar)


def combinada_del_partido_a_dict(
    combo: Combinada, *, model_version: str, odds_captured_at: datetime | None,
) -> dict[str, Any]:
    """Como :func:`combinada_a_dict` (que ya marca ``experimental`` y el jugador de cada prop),
    con los datos del partido."""
    out = combinada_a_dict(combo, model_version=model_version, odds_captured_at=odds_captured_at)
    c0 = combo.patas[0]
    out.update({
        "match_id": c0.match_id,
        "home_team": c0.home_team,
        "away_team": c0.away_team,
        "league": c0.league,
        "commence_time": c0.commence_time.isoformat(),
    })
    return out


def huella_picks(
    picks_json: list[dict[str, Any]],
    combo_json: dict[str, Any] | None,
    partido_json: dict[str, Any] | None = None,
) -> str:
    """SHA-256 del JSON canónico de simples + combinada (+ combinada del partido, si la hay;
    sin ella, la huella es la de siempre y las filas antiguas siguen cuadrando)."""
    datos: dict[str, Any] = {"simples": picks_json, "combinada": combo_json}
    if partido_json is not None:
        datos["combinada_partido"] = partido_json
    canonico = json.dumps(
        datos,
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


__all__ = [
    "COMBO_MAX_PATAS_POR_GRUPO",
    "GRUPO_DE_MERCADO",
    "PARTIDO_TOTAL_MAX",
    "PARTIDO_TOTAL_MIN",
    "PREFERENCIA_PROPS",
    "combinada_del_partido",
    "combinada_del_partido_a_dict",
    "elegir_combinada_del_partido",
    "enumerar_combinadas_del_partido",
    "es_prop",
    "patas_para_combinada_del_partido",
    "pesos_de_pata",
    "puntuar_combinada_del_partido",
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
    "grupo_de_mercado",
    "grupos_distintos",
    "grupos_para_combinada",
    "huella_picks",
    "mascara_de_pata",
    "pata_a_dict",
    "sin_selecciones",
    "puntuar_combinada",
    "rejilla_marcadores",
]
