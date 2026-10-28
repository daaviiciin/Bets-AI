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

**Encogimiento.** Con 10 partidos el ritmo es ruidoso: se encoge hacia el de su posición (D, M, F,
G) con ``k`` unidades de exposición de peso: ``r = (Σx + k·r_posición) / (Σexposición + k)``.

**Minutos.** Las casas anulan el prop si el jugador no juega, así que todo va **condicionado a que
juegue**: los minutos esperados salen solo de las apariciones con minutos.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.services import poisson_model as pm

Estadistica = Literal["tiros_puerta", "goles", "paradas"]

#: Peso del xG frente a los goles en el ritmo de goleador (los goles solos son muy ruidosos).
PESO_XG_GOLEADOR = 0.5
#: Encogimiento por defecto, en unidades de exposición (≈ partidos completos con λ = 1).
K_ENCOGIMIENTO = 3.0
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


def _exposicion(a: Aparicion, est: Estadistica) -> float:
    lam = a.lambda_rival if est == "paradas" else a.lambda_equipo
    return a.minutos / 90.0 * lam


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
    k: float = K_ENCOGIMIENTO,
    minutos: Callable[[Sequence[Aparicion]], float | None] = minutos_esperados,
) -> float | None:
    """Valor esperado de la estadística en el partido de hoy (``None`` sin minutos)."""
    mins = minutos(apariciones)
    if mins is None or mins <= 0:
        return None
    lam = lambda_rival if est == "paradas" else lambda_equipo
    return ritmo_encogido(apariciones, est, prior, k=k) * mins / 90.0 * lam


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


__all__ = [
    "Aparicion",
    "K_ENCOGIMIENTO",
    "POSICIONES",
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

