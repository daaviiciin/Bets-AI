"""Modelo de props de jugador ajustado al rival (fase 3, bloque B14b).

Funciones puras sobre las apariciones previas de un jugador (``player_match_stats``) y los goles
esperados (λ) del modelo de equipos v2. Sustituye a los ritmos de temporada de
``value_detector`` (tiros a puerta y goleador sin tener en cuenta al rival; paradas sin modelo).

**Ritmo por gol esperado.** El ritmo de un jugador no se mide por 90 minutos sino por cada gol
que se esperaba de su equipo en los partidos que jugó::

    r = Σ tiros a puerta / Σ (minutos/90 × λ_equipo de ese partido)

Así se descuenta contra quién los hizo. Para el partido de hoy, ``μ = r × minutos/90 × λ_equipo``
(Poisson). Las **paradas** van igual con el λ del **rival** (cuanto más se espera que ataque, más
paradas), y el **goleador** con una mezcla de goles y xG: ``P = 1 − e^(−μ)``, que es la misma P que
da el *thinning* de los goles del equipo (B14d la usa sobre la matriz de marcadores).

**Exposición por tiros a puerta (B6).** Si hay modelo de tiros a puerta de equipo
(``sot_model``), los tiros a puerta del jugador se miden por cada tiro a puerta esperado de **su**
equipo y las paradas por cada tiro a puerta esperado del **rival** (``Aparicion.sot_equipo`` /
``sot_rival``): es la parte de los tiros del equipo que hace el jugador, o la de los del rival que
para el portero. Sin ese modelo, la exposición sigue siendo el λ de goles.

**Encogimiento.** Con 10 partidos el ritmo es ruidoso: se encoge hacia el de su posición (D, M, F,
G) con ``k`` unidades de exposición de peso: ``r = (Σx + k·r_posición) / (Σexposición + k)``.

**Minutos.** Las casas anulan el prop si el jugador no juega, así que todo va **condicionado a que
juegue**: los minutos esperados salen solo de las apariciones con minutos.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from backend.services import poisson_model as pm

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

Estadistica = Literal["tiros_puerta", "goles", "paradas"]

#: Peso del xG frente a los goles en el ritmo de goleador (los goles solos son muy ruidosos).
PESO_XG_GOLEADOR = 0.5
#: Encogimiento por defecto, en unidades de exposición (≈ partidos completos con λ = 1).
K_ENCOGIMIENTO = 3.0
#: El mismo encogimiento cuando la exposición son tiros a puerta esperados (≈ 3,3 por gol).
K_ENCOGIMIENTO_SOT = 10.0
POSICIONES = ("G", "D", "M", "F")
#: Minutos esperados: últimas apariciones con minutos, semivida (en apariciones) y mínimo.
MINUTOS_VENTANA = 6
MINUTOS_SEMIVIDA = 3.0
MINUTOS_MIN = 15.0


@dataclass(frozen=True)
class Aparicion:
    """Un partido ya jugado por el jugador, con los λ que el modelo daba a cada equipo."""

    fecha: datetime
    minutos: int
    titular: bool | None
    tiros_puerta: int | None
    goles: int | None
    xg: float | None
    paradas: int | None
    #: Goles esperados de su equipo y del rival en ese partido.
    lambda_equipo: float
    lambda_rival: float
    #: Tiros a puerta esperados de su equipo y del rival (``sot_model``; ``None`` sin modelo).
    sot_equipo: float | None = None
    sot_rival: float | None = None


def _valor(a: Aparicion, est: Estadistica) -> float | None:
    if est == "tiros_puerta":
        return None if a.tiros_puerta is None else float(a.tiros_puerta)
    if est == "paradas":
        return None if a.paradas is None else float(a.paradas)
    if a.goles is None:
        return None
    if a.xg is None:
        return float(a.goles)
    return (1 - PESO_XG_GOLEADOR) * a.goles + PESO_XG_GOLEADOR * a.xg


def _base_exposicion(
    est: Estadistica, lambda_equipo: float, lambda_rival: float,
    sot_equipo: float | None, sot_rival: float | None,
) -> float:
    """Lo que mide la exposición de cada estadística: tiros a puerta esperados de su equipo
    (tiros a puerta) o del rival (paradas) si los hay; si no, el λ de goles."""
    if est == "paradas":
        return sot_rival if sot_rival is not None else lambda_rival
    if est == "tiros_puerta" and sot_equipo is not None:
        return sot_equipo
    return lambda_equipo


def _exposicion(a: Aparicion, est: Estadistica) -> float:
    base = _base_exposicion(est, a.lambda_equipo, a.lambda_rival, a.sot_equipo, a.sot_rival)
    return a.minutos / 90.0 * base


def suma_ritmo(apariciones: Iterable[Aparicion], est: Estadistica) -> tuple[float, float]:
    """``(Σ estadística, Σ exposición)`` de las apariciones con minutos y dato."""
    total = expo = 0.0
    for a in apariciones:
        v = _valor(a, est)
        if a.minutos <= 0 or v is None:
            continue
        total += v
        expo += _exposicion(a, est)
    return total, expo


def ritmos_por_posicion(
    por_posicion: Iterable[tuple[str | None, Aparicion]],
) -> dict[tuple[str, Estadistica], float]:
    """Ritmo medio (por gol esperado) de cada posición y estadística, para encoger."""
    acum: dict[tuple[str, Estadistica], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for pos, a in por_posicion:
        if pos not in POSICIONES:
            continue
        for est in ("tiros_puerta", "goles", "paradas"):
            total, expo = suma_ritmo([a], est)
            acum[(pos, est)][0] += total
            acum[(pos, est)][1] += expo
    return {clave: t / e for clave, (t, e) in acum.items() if e > 0}


def ritmo_encogido(
    apariciones: Iterable[Aparicion], est: Estadistica, prior: float, *, k: float = K_ENCOGIMIENTO,
) -> float:
    total, expo = suma_ritmo(apariciones, est)
    return (total + k * prior) / (expo + k)


def minutos_esperados(apariciones: Sequence[Aparicion]) -> float | None:
    """Minutos que se esperan del jugador en su próximo partido **si juega** (``None`` si no
    hay ninguna aparición con minutos).

    ``apariciones`` va ordenada de la más antigua a la más reciente y solo trae los partidos en
    los que estuvo convocado (las de 0 minutos son suplentes que no salieron).
    """
    # Media de las últimas apariciones con minutos, pesando más las recientes: ve antes que la
    # media de temporada que alguien ha perdido (o ganado) el puesto, y una rotación suelta
    # pesa poco. Los 0 minutos no cuentan: si no juega, el prop se anula.
    jugadas = [a.minutos for a in apariciones if a.minutos > 0][-MINUTOS_VENTANA:]
    if not jugadas:
        return None
    pesos = [0.5 ** (edad / MINUTOS_SEMIVIDA) for edad in range(len(jugadas) - 1, -1, -1)]
    media = sum(p * m for p, m in zip(pesos, jugadas)) / sum(pesos)
    return min(max(media, MINUTOS_MIN), 90.0)


def mu_partido(
    apariciones: Sequence[Aparicion],
    est: Estadistica,
    *,
    prior: float,
    lambda_equipo: float,
    lambda_rival: float,
    sot_equipo: float | None = None,
    sot_rival: float | None = None,
    k: float = K_ENCOGIMIENTO,
    minutos: Callable[[Sequence[Aparicion]], float | None] = minutos_esperados,
) -> float | None:
    """Valor esperado de la estadística en el partido de hoy (``None`` sin minutos).

    ``sot_equipo`` / ``sot_rival`` (tiros a puerta esperados hoy) solo se usan si las
    apariciones también los traen: el ritmo y la exposición tienen que ir en la misma unidad.
    """
    mins = minutos(apariciones)
    if mins is None or mins <= 0:
        return None
    usa_sot = bool(apariciones) and apariciones[-1].sot_equipo is not None
    if not usa_sot:
        sot_equipo = sot_rival = None
    elif (est == "tiros_puerta" and sot_equipo is None) or (est == "paradas" and sot_rival is None):
        return None
    base = _base_exposicion(est, lambda_equipo, lambda_rival, sot_equipo, sot_rival)
    return ritmo_encogido(apariciones, est, prior, k=k) * mins / 90.0 * base


def p_mas_de(mu: float, linea: float) -> float:
    """P(estadística > línea) con un Poisson de media ``mu`` (``linea`` = 0,5, 1,5…)."""
    over, _ = pm.probability_over_single_poisson(mu, linea)
    return over


def p_goleador(mu_goles: float, goles: int = 1) -> float:
    """P(marca al menos ``goles``)."""
    return p_mas_de(mu_goles, goles - 0.5)


def q_thinning(mu_goles: float, lambda_equipo: float) -> float:
    """Parte de los goles de su equipo que marca el jugador (``μ = q·λ``), para B14d."""
    if lambda_equipo <= 0:
        return 0.0
    return min(1.0, mu_goles / lambda_equipo)


def p_goleador_dado_goles_equipo(q: float, goles_equipo: int) -> float:
    """P(marca | su equipo marca ``goles_equipo``) = 1 − (1 − q)^k."""
    return 1.0 - (1.0 - q) ** goles_equipo


# ---------------------------------------------------------------------------
# Líneas de Winamax: mercados, lado y probabilidad (B14b)
# ---------------------------------------------------------------------------

#: Mercados de props que cubre el modelo y la estadística de cada uno.
MERCADOS_PROPS: dict[str, Estadistica] = {
    "player_sot": "tiros_puerta",
    "player_sot_alt": "tiros_puerta",
    "player_anytime_scorer": "goles",
    "player_to_score_2plus": "goles",
    "player_saves": "paradas",
    "player_saves_alt": "paradas",
}
#: Familia de cada mercado de props (backtest, calibración y combinada del partido).
FAMILIA_PROP: dict[str, str] = {
    "player_sot": "player_sot",
    "player_sot_alt": "player_sot",
    "player_anytime_scorer": "goleador",
    "player_to_score_2plus": "goleador",
    "player_saves": "paradas",
    "player_saves_alt": "paradas",
}
FAMILIAS_PROPS = ("player_sot", "goleador", "paradas")
#: Goles que tiene que marcar el jugador en cada mercado de goleador.
GOLES_GOLEADOR: dict[str, int] = {"player_anytime_scorer": 1, "player_to_score_2plus": 2}
#: Apariciones con minutos que necesita un jugador para tener P del modelo.
MIN_APARICIONES = 3

_RE_MAS = re.compile(r"\b(over|m[áa]s)\b", re.I)
_RE_MENOS = re.compile(r"\b(under|menos)\b", re.I)


def familia_prop(market_key: str) -> str | None:
    return FAMILIA_PROP.get(market_key)


def lado_prop(market_key: str, outcome_name: str) -> Literal["over", "under"] | None:
    """``over`` / ``under`` de una línea de props. En goleador el resultado es el nombre del
    jugador (siempre "marca")."""
    if market_key in GOLES_GOLEADOR:
        return "over"
    texto = outcome_name or ""
    if _RE_MENOS.search(texto):
        return "under"
    if _RE_MAS.search(texto):
        return "over"
    return None


def linea_prop(market_key: str, outcome_point: float | None) -> float | None:
    """Línea "más de X" del mercado (en goleador, 0,5 o 1,5)."""
    if market_key in GOLES_GOLEADOR:
        return GOLES_GOLEADOR[market_key] - 0.5
    return None if outcome_point is None else float(outcome_point)


@dataclass(frozen=True)
class PartidoProps:
    """Lo que espera el modelo de equipos del partido de hoy, visto desde el lado del jugador."""

    lambda_equipo: float
    lambda_rival: float
    sot_equipo: float | None = None
    sot_rival: float | None = None
    #: Tamaño de la binomial negativa de los tiros a puerta de equipo (``None`` = Poisson).
    r_sot: float | None = None


@dataclass(frozen=True)
class JugadorProps:
    player_id: int
    #: Posición más frecuente en sus partidos (G, D, M, F) o ``None``.
    posicion: str | None
    #: Equipo de su última aparición.
    team_id: int
    #: De la más antigua a la más reciente.
    apariciones: tuple[Aparicion, ...]


@dataclass(frozen=True)
class ContextoProps:
    """Todo lo que hace falta para dar P a las líneas de props de un día (sin BD)."""

    jugadores: dict[int, JugadorProps]
    priors: dict[tuple[str, Estadistica], float] = field(default_factory=dict)
    #: Ritmo de todos los jugadores, para los que no tienen posición.
    prior_global: dict[Estadistica, float] = field(default_factory=dict)

    def lado(self, player_id: int, home_id: int, away_id: int) -> Literal["home", "away"] | None:
        """Lado del partido en el que juega (según su última aparición)."""
        j = self.jugadores.get(player_id)
        if j is None:
            return None
        if j.team_id == home_id:
            return "home"
        if j.team_id == away_id:
            return "away"
        return None

    def mu(self, player_id: int, est: Estadistica, partido: PartidoProps) -> float | None:
        """Valor esperado de la estadística hoy, si juega (``None`` sin datos suficientes)."""
        j = self.jugadores.get(player_id)
        if j is None or sum(a.minutos > 0 for a in j.apariciones) < MIN_APARICIONES:
            return None
        # Paradas solo de porteros; goles y tiros a puerta, de cualquiera menos el portero.
        if (est == "paradas") != (j.posicion == "G"):
            return None
        prior = self.priors.get((j.posicion or "", est), self.prior_global.get(est))
        if prior is None:
            return None
        usa_sot = est != "goles" and j.apariciones[-1].sot_equipo is not None
        return mu_partido(
            j.apariciones, est, prior=prior,
            lambda_equipo=partido.lambda_equipo, lambda_rival=partido.lambda_rival,
            sot_equipo=partido.sot_equipo, sot_rival=partido.sot_rival,
            k=K_ENCOGIMIENTO_SOT if usa_sot else K_ENCOGIMIENTO,
        )

    def probabilidad(
        self,
        player_id: int,
        market_key: str,
        outcome_name: str,
        outcome_point: float | None,
        partido: PartidoProps,
    ) -> float | None:
        """P (sin calibrar) de una línea de props, condicionada a que el jugador juegue."""
        est = MERCADOS_PROPS.get(market_key)
        lado = lado_prop(market_key, outcome_name)
        linea = linea_prop(market_key, outcome_point)
        if est is None or lado is None or linea is None:
            return None
        if lado == "under" and math.isclose(linea % 1, 0.0):
            return None  # línea entera: el empate se anula y no lo modelamos
        mu = self.mu(player_id, est, partido)
        if mu is None:
            return None
        p = p_mas_de(mu, linea)
        return p if lado == "over" else 1.0 - p

    def vector_condicional(
        self,
        player_id: int,
        market_key: str,
        outcome_name: str,
        outcome_point: float | None,
        partido: PartidoProps,
        *,
        size: int = 11,
    ) -> tuple[Literal["equipo", "rival"], tuple[float, ...]] | None:
        """P de la línea **condicionada a los goles** de su equipo (goleador, tiros a puerta) o del
        rival (paradas), para la matriz de marcadores de la combinada del partido (B14d).

        Devuelve ``(de quién son los goles, v)`` con ``v[k] = P(gana la línea | k goles)``.
        """
        est = MERCADOS_PROPS.get(market_key)
        lado = lado_prop(market_key, outcome_name)
        linea = linea_prop(market_key, outcome_point)
        if est is None or lado is None or linea is None:
            return None
        if lado == "under" and math.isclose(linea % 1, 0.0):
            return None
        mu = self.mu(player_id, est, partido)
        if mu is None:
            return None
        quien: Literal["equipo", "rival"] = "equipo"
        if est == "goles":
            v = vector_goleador(q_thinning(mu, partido.lambda_equipo), GOLES_GOLEADOR[market_key], size)
        elif est == "tiros_puerta":
            mu_g = self.mu(player_id, "goles", partido) or 0.0
            v = vector_tiros_puerta(mu, mu_g, partido.lambda_equipo, linea, size)
        else:
            v = vector_paradas(mu, partido.lambda_rival, partido.r_sot, linea, size)
            quien = "rival"
        if lado == "under":
            v = tuple(1.0 - x for x in v)
        return quien, v


def vector_goleador(q: float, goles: int, size: int) -> tuple[float, ...]:
    """P(marca al menos ``goles`` | su equipo marca k), k = 0..size−1 (binomial de k y q)."""
    out = []
    for k in range(size):
        p_menos = sum(math.comb(k, j) * q**j * (1 - q) ** (k - j) for j in range(min(goles, k + 1)))
        out.append(1.0 - p_menos)
    return tuple(out)


def vector_tiros_puerta(
    mu_sot: float, mu_goles: float, lambda_equipo: float, linea: float, size: int,
) -> tuple[float, ...]:
    """P(tiros a puerta > ``linea`` | su equipo marca k).

    Cada gol suyo es un tiro a puerta: tiros = goles del jugador (binomial de k con la parte
    ``q`` de los goles del equipo que marca) + el resto de sus tiros a puerta (Poisson
    independiente del marcador). Sumando sobre k ~ Poisson(λ) vuelve a dar Poisson(μ_sot).
    """
    q = q_thinning(min(mu_goles, mu_sot), lambda_equipo) if lambda_equipo > 0 else 0.0
    resto = max(mu_sot - q * lambda_equipo, 0.0)
    out = []
    for k in range(size):
        total = 0.0
        for j in range(k + 1):
            pj = math.comb(k, j) * q**j * (1 - q) ** (k - j)
            falta = linea - j
            if falta < 0:
                total += pj
            elif resto > 0:
                total += pj * p_mas_de(resto, falta)
        out.append(min(max(total, 0.0), 1.0))
    return tuple(out)


def vector_paradas(
    mu_paradas: float, lambda_rival: float, r_sot: float | None, linea: float, size: int,
) -> tuple[float, ...]:
    """P(paradas > ``linea`` | el rival marca k).

    Los tiros a puerta del rival tienen una intensidad incierta (gamma de tamaño ``r_sot``, la
    binomial negativa de ``sot_model``); goles y paradas son dos partes de esos tiros. Ver k goles
    sube la intensidad esperada: ``E[paradas | k] = μ·(r + k)/(r + λ_rival)``, con una binomial
    negativa de tamaño ``r + k``. Sin sobredispersión (``r_sot=None``), paradas y goles son
    independientes y el vector es constante.
    """
    from backend.services import sot_model

    if r_sot is None:
        return tuple(p_mas_de(mu_paradas, linea) for _ in range(size))
    return tuple(
        sot_model.p_mas_de(mu_paradas * (r_sot + k) / (r_sot + lambda_rival), linea, r_sot + k)
        for k in range(size)
    )


# ---------------------------------------------------------------------------
# Contexto desde las filas de ``player_match_stats``
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilaJugador:
    """Una fila de ``player_match_stats`` con lo que hace falta del partido."""

    player_id: int
    match_id: int
    team_id: int
    is_home: bool
    fecha: datetime
    sport_key: str
    home_id: int
    away_id: int
    minutos: int
    titular: bool | None
    posicion: str | None
    tiros_puerta: int | None
    goles: int | None
    xg: float | None
    paradas: int | None


def construir_contexto(
    filas: Iterable[FilaJugador],
    lambdas: Callable[[str, int, int], tuple[float, float]],
    sots: Callable[[str, int, int], tuple[float, float]] | None = None,
    *,
    cutoff: datetime | None = None,
) -> ContextoProps:
    """Apariciones de cada jugador con los λ (y tiros a puerta esperados) del modelo de equipos.

    ``lambdas`` / ``sots`` = ``(sport_key, local, visitante) → (local, visitante)``, del ajuste
    hecho con datos anteriores a ``cutoff``. Con ``cutoff``, falla si llega un partido posterior
    (sin fuga, como ``assert_sin_fuga``).
    """
    filas = sorted(filas, key=lambda f: (f.fecha, f.match_id, f.player_id))
    if cutoff is not None:
        tarde = [f.match_id for f in filas if f.fecha >= cutoff]
        if tarde:
            raise ValueError(f"Fuga de datos: {len(tarde)} filas de jugador en o después del corte")
    por_jugador: dict[int, list[tuple[FilaJugador, Aparicion]]] = defaultdict(list)
    cache: dict[int, tuple[float, float, float | None, float | None]] = {}
    for f in filas:
        if f.match_id not in cache:
            lh, la = lambdas(f.sport_key, f.home_id, f.away_id)
            sh, sa = sots(f.sport_key, f.home_id, f.away_id) if sots is not None else (None, None)
            cache[f.match_id] = (lh, la, sh, sa)
        lh, la, sh, sa = cache[f.match_id]
        ap = Aparicion(
            fecha=f.fecha, minutos=f.minutos, titular=f.titular, tiros_puerta=f.tiros_puerta,
            goles=f.goles, xg=f.xg, paradas=f.paradas,
            lambda_equipo=lh if f.is_home else la, lambda_rival=la if f.is_home else lh,
            sot_equipo=sh if f.is_home else sa, sot_rival=sa if f.is_home else sh,
        )
        por_jugador[f.player_id].append((f, ap))

    jugadores: dict[int, JugadorProps] = {}
    por_posicion: list[tuple[str | None, Aparicion]] = []
    for pid, lista in por_jugador.items():
        posiciones = Counter(f.posicion for f, a in lista if f.posicion in POSICIONES and a.minutos > 0)
        pos = posiciones.most_common(1)[0][0] if posiciones else None
        jugadores[pid] = JugadorProps(pid, pos, lista[-1][0].team_id, tuple(a for _, a in lista))
        por_posicion.extend((pos, a) for _, a in lista)

    prior_global: dict[Estadistica, float] = {}
    for est in ("tiros_puerta", "goles", "paradas"):
        aps = [a for pos, a in por_posicion if (pos == "G") == (est == "paradas")]
        total, expo = suma_ritmo(aps, est)
        if expo > 0:
            prior_global[est] = total / expo
    return ContextoProps(jugadores, ritmos_por_posicion(por_posicion), prior_global)


def cargar_filas_jugador(session: Session, cutoff: datetime) -> list[FilaJugador]:
    """Filas de ``player_match_stats`` de partidos de fútbol terminados antes de ``cutoff``."""
    from sqlalchemy import select

    from backend.models import League, Match, PlayerMatchStats
    from backend.models.match import MATCH_STATUS_FINISHED

    filas = session.execute(
        select(
            PlayerMatchStats, Match.commence_time, League.sport_key,
            Match.home_team_id, Match.away_team_id,
        )
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .join(League, League.id == Match.league_id)
        .where(Match.status == MATCH_STATUS_FINISHED, Match.commence_time < cutoff)
        .where(~League.sport_key.ilike("%basketball%")),
    ).all()
    out = []
    for pms, fecha, sk, home_id, away_id in filas:
        if pms.minutes_played is None:
            continue
        out.append(FilaJugador(
            player_id=pms.player_id, match_id=pms.match_id, team_id=pms.team_id,
            is_home=bool(pms.is_home), fecha=fecha, sport_key=sk or "", home_id=home_id,
            away_id=away_id, minutos=int(pms.minutes_played), titular=pms.is_starter,
            posicion=pms.position_in_match, tiros_puerta=pms.shots_on_target, goles=pms.goals,
            xg=float(pms.expected_goals) if pms.expected_goals is not None else None,
            paradas=pms.saves,
        ))
    return out


__all__ = [
    "Aparicion",
    "ContextoProps",
    "FAMILIAS_PROPS",
    "FAMILIA_PROP",
    "FilaJugador",
    "JugadorProps",
    "K_ENCOGIMIENTO",
    "K_ENCOGIMIENTO_SOT",
    "MERCADOS_PROPS",
    "MIN_APARICIONES",
    "POSICIONES",
    "PartidoProps",
    "cargar_filas_jugador",
    "construir_contexto",
    "familia_prop",
    "lado_prop",
    "linea_prop",
    "vector_goleador",
    "vector_paradas",
    "vector_tiros_puerta",
    "minutos_esperados",
    "mu_partido",
    "p_goleador",
    "p_goleador_dado_goles_equipo",
    "p_mas_de",
    "q_thinning",
    "ritmo_encogido",
    "ritmos_por_posicion",
    "suma_ritmo",
]

