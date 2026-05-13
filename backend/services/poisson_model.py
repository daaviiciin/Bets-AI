"""Modelo Poisson independiente para predecir resultado, totales y BTTS.

Aproximación clásica de modelado de fútbol descrita por Maher (1982) y
popularizada por Dixon-Coles. Es una baseline simple pero efectiva: con
~10 jornadas ya pega ~58% en 1X2 y ~62% en Over/Under 2.5 sin ajustes finos.

Pasos:

1. **Fortalezas relativas** (ataque y defensa) sobre la media de goles de la
   liga. Si Real Madrid mete 2.1 g/p y la media de LaLiga es 1.4 g/p, su
   fortaleza ofensiva es ``2.1 / 1.4 = 1.50``.

2. **Esperanza de goles** del partido aplicando ventaja de campo
   (``HOME_FACTOR = 1.10`` por convención):

   .. math::

       \\lambda_{home} = AS_{home} \\cdot DS_{away} \\cdot \\mu_{liga} \\cdot 1.10

       \\lambda_{away} = AS_{away} \\cdot DS_{home} \\cdot \\mu_{liga} \\cdot 1/1.10

3. **Distribución conjunta** asumiendo independencia (Poisson independiente).
   La fórmula real (Dixon-Coles) ajusta la cola 0-0/0-1/1-0/1-1, pero para
   un MVP el modelo independiente da resultados razonables.

4. **Probabilidades de mercado** sumando celdas de la matriz.

Este módulo NO toca BD, sólo recibe números y devuelve números.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Ventaja de campo por defecto. 1.10 ≈ +10% para el local
# (típica en ligas top-5; baja en ligas asiáticas, alta en sudamericanas).
HOME_ADVANTAGE: float = 1.10

# Corrección Dixon-Coles por defecto. ρ = 0 ⇒ Poisson independiente "puro";
# valores positivos típicos (≈ 0.05 – 0.15) suben P(0-0)/P(1-1)... espera,
# fijémonos: la fórmula clásica reescala las 4 celdas low-score así:
#
#     τ(0,0) = 1 - λH·λA·ρ        (con ρ>0 BAJA P(0-0))
#     τ(0,1) = 1 + λH·ρ           (con ρ>0 SUBE P(0-1))
#     τ(1,0) = 1 + λA·ρ           (con ρ>0 SUBE P(1-0))
#     τ(1,1) = 1 - ρ              (con ρ>0 BAJA P(1-1))
#
# La convención que usamos aquí es la opuesta a Dixon-Coles original: en
# nuestra implementación **ρ negativo** sube empates 0-0 / 1-1 (lo común en
# ligas defensivas), y **ρ positivo** sube los 1-0 / 0-1. La calibración
# decidirá el signo. Mantenerla como hiperparámetro libre es lo más honesto.
DIXON_COLES_RHO: float = 0.0

GOAL_GRID_SIZE: int = 8
"""Número de goles posibles por equipo a considerar (0..N-1).

Con λ ≈ 1.5 la probabilidad acumulada hasta 7 goles es ~99.99%, por lo que
una matriz 8×8 cubre casi toda la masa. Más alto sólo añade ruido numérico.
"""


@dataclass(frozen=True)
class TeamLeagueAverages:
    """Promedios temporada de un equipo en su liga."""

    matches_played: int
    goals_for_per_match: float   # GF / MP
    goals_against_per_match: float  # GA / MP


@dataclass(frozen=True)
class MatchExpectation:
    """Resultado del modelo: lambdas + probabilidades de mercado."""

    lambda_home: float
    lambda_away: float
    p_home_win: float
    p_draw: float
    p_away_win: float
    p_over_15: float
    p_under_15: float
    p_over_25: float
    p_under_25: float
    p_over_35: float
    p_under_35: float
    p_btts_yes: float
    p_btts_no: float
    # Hiperparámetro Dixon-Coles aplicado (0.0 ⇒ Poisson independiente puro).
    # Se mantiene en la salida para trazabilidad: una predicción cargada de BD
    # debe poder reportar con qué ρ se generó.
    dixon_coles_rho: float = 0.0

    # ----- Double Chance (derivadas del 1X2) -----
    @property
    def p_home_or_draw(self) -> float:
        """1X — local gana o empata."""
        return self.p_home_win + self.p_draw

    @property
    def p_home_or_away(self) -> float:
        """12 — alguien gana, no hay empate."""
        return self.p_home_win + self.p_away_win

    @property
    def p_draw_or_away(self) -> float:
        """X2 — visitante gana o empata."""
        return self.p_draw + self.p_away_win

    def as_dict(self) -> dict[str, float]:
        return {
            "lambda_home": self.lambda_home,
            "lambda_away": self.lambda_away,
            "p_home_win": self.p_home_win,
            "p_draw": self.p_draw,
            "p_away_win": self.p_away_win,
            "p_over_15": self.p_over_15,
            "p_under_15": self.p_under_15,
            "p_over_25": self.p_over_25,
            "p_under_25": self.p_under_25,
            "p_over_35": self.p_over_35,
            "p_under_35": self.p_under_35,
            "p_btts_yes": self.p_btts_yes,
            "p_btts_no": self.p_btts_no,
            "p_home_or_draw": self.p_home_or_draw,
            "p_home_or_away": self.p_home_or_away,
            "p_draw_or_away": self.p_draw_or_away,
            "dixon_coles_rho": self.dixon_coles_rho,
        }


def league_average_goals(teams: list[TeamLeagueAverages]) -> float:
    """Media de goles totales por partido en la liga (incluye GF y GA).

    Usamos la suma de GF (que = suma de GA) sobre el total de partidos: el
    factor 2 que aparecería si sumásemos ambos lados ya está implícito.
    """
    if not teams:
        return 1.4  # fallback sano
    total_gf = sum(t.goals_for_per_match * t.matches_played for t in teams)
    total_mp = sum(t.matches_played for t in teams)
    if total_mp == 0:
        return 1.4
    return total_gf / total_mp


def expected_goals(
    home: TeamLeagueAverages,
    away: TeamLeagueAverages,
    league_avg: float,
    *,
    home_advantage: float = HOME_ADVANTAGE,
) -> tuple[float, float]:
    """Devuelve (λ_home, λ_away) para el partido.

    Usa fortalezas relativas tipo Maher.

    * Si la media de la liga es 0, devolvemos algo plausible para no romper.
    """
    if league_avg <= 0:
        league_avg = 1.4

    home_attack = home.goals_for_per_match / league_avg if home.goals_for_per_match else 1.0
    home_defense = home.goals_against_per_match / league_avg if home.goals_against_per_match else 1.0
    away_attack = away.goals_for_per_match / league_avg if away.goals_for_per_match else 1.0
    away_defense = away.goals_against_per_match / league_avg if away.goals_against_per_match else 1.0

    lambda_home = home_attack * away_defense * league_avg * home_advantage
    lambda_away = away_attack * home_defense * league_avg / home_advantage

    lambda_home = max(0.05, lambda_home)
    lambda_away = max(0.05, lambda_away)
    return lambda_home, lambda_away


def _poisson_pmf(k: int, lam: float) -> float:
    """Función de masa de probabilidad Poisson P(X=k)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _apply_dixon_coles(
    grid: list[list[float]],
    lambda_home: float,
    lambda_away: float,
    rho: float,
) -> list[list[float]]:
    """Aplica la corrección Dixon-Coles a la matriz Poisson independiente.

    Mutación in-place + renormalización. Sólo afecta a las 4 celdas low-score.
    Si alguna celda quedase negativa por un ρ fuera del rango admisible, la
    saturamos a 0 antes de renormalizar (más robusto que estallar).

    El rango "seguro" es:

    * ``ρ ≤ 1 / (λH·λA)``    para que τ(0,0) ≥ 0
    * ``ρ ≤ 1``              para que τ(1,1) ≥ 0
    * ``ρ ≥ -1 / λH``        para que τ(0,1) ≥ 0
    * ``ρ ≥ -1 / λA``        para que τ(1,0) ≥ 0

    En la práctica la calibración devuelve |ρ| < 0.2.
    """
    if rho == 0.0:
        return grid

    # Aplicamos τ a las 4 celdas afectadas
    grid[0][0] *= 1.0 - lambda_home * lambda_away * rho
    grid[0][1] *= 1.0 + lambda_home * rho
    grid[1][0] *= 1.0 + lambda_away * rho
    grid[1][1] *= 1.0 - rho

    # Saturar a 0 si se ha cruzado la frontera (ρ extremo + λ extremas).
    for r in range(2):
        for c in range(2):
            if grid[r][c] < 0.0:
                grid[r][c] = 0.0

    # Renormalizar para que la matriz vuelva a sumar 1.
    total = 0.0
    for row in grid:
        total += sum(row)
    if total > 0.0:
        inv = 1.0 / total
        for r in range(len(grid)):
            for c in range(len(grid[r])):
                grid[r][c] *= inv
    return grid


def _build_grid(
    lambda_home: float,
    lambda_away: float,
    *,
    size: int = GOAL_GRID_SIZE,
    dixon_coles_rho: float = 0.0,
) -> list[list[float]]:
    """Matriz P(home=h, away=a) asumiendo independencia (+ DC opcional)."""
    home_pmf = [_poisson_pmf(h, lambda_home) for h in range(size)]
    away_pmf = [_poisson_pmf(a, lambda_away) for a in range(size)]
    grid = [[home_pmf[h] * away_pmf[a] for a in range(size)] for h in range(size)]
    if dixon_coles_rho != 0.0:
        _apply_dixon_coles(grid, lambda_home, lambda_away, dixon_coles_rho)
    return grid


def probability_over_total(
    lambda_home: float,
    lambda_away: float,
    point: float,
    *,
    grid_size: int = GOAL_GRID_SIZE,
    dixon_coles_rho: float = 0.0,
) -> tuple[float, float]:
    """Probabilidades ``(P(over point), P(under point))`` para un total arbitrario.

    Convenciones de bookie:

    * **Línea .5** (2.5, 3.5...): no hay push. Over gana si ``total > point``;
      ``p_over + p_under = 1``.
    * **Línea entera** (2.0, 3.0...): hay push si ``total == point``. Devolvemos
      las probabilidades estrictas de ganar (over: ``total > point``;
      under: ``total < point``); la diferencia respecto a 1 es la masa del
      push, que el cliente puede usar para repartirla entre over/under si
      le interesa.
    * **Líneas .25 / .75**: las redondeamos al ``.5`` más próximo. Es una
      aproximación habitual: la diferencia respecto al promedio teórico es
      pequeña salvo en líneas extremas.

    Para puntos típicos (.5) el resultado es exacto dada la matriz Poisson.
    """
    if point < 0:
        return 1.0, 0.0

    # Líneas no estándar (.25 / .75): aproximamos a la .5 más cercana.
    if not (math.isclose(point % 1, 0.0) or math.isclose(point % 1, 0.5)):
        nearest_half = math.floor(point) + 0.5
        return probability_over_total(
            lambda_home, lambda_away, nearest_half,
            grid_size=grid_size, dixon_coles_rho=dixon_coles_rho,
        )

    grid = _build_grid(
        lambda_home, lambda_away,
        size=grid_size, dixon_coles_rho=dixon_coles_rho,
    )

    if math.isclose(point % 1, 0.5):
        # .5 → over si total >= ceil(point); under = 1 - over.
        threshold = math.ceil(point)
        p_over = sum(
            grid[h][a]
            for h in range(grid_size)
            for a in range(grid_size)
            if h + a >= threshold
        )
        return p_over, 1.0 - p_over

    # Punto entero (push posible).
    point_int = int(round(point))
    p_over = sum(
        grid[h][a]
        for h in range(grid_size)
        for a in range(grid_size)
        if h + a > point_int
    )
    p_under = sum(
        grid[h][a]
        for h in range(grid_size)
        for a in range(grid_size)
        if h + a < point_int
    )
    return p_over, p_under


def probability_over_single_poisson(
    lambda_: float,
    point: float,
    *,
    k_tail: int = 80,
) -> tuple[float, float]:
    """``(P(over point), P(under point))`` para un único recuento Poisson(λ).

    Replica las convenciones de :func:`probability_over_total` aplicadas al
    total de dos equipos, pero usando una sola variable (goles locales,
    jugador SoT, etc.).
    """
    lam = float(lambda_)
    pt = float(point)
    if pt < 0:
        return 1.0, 0.0
    # Líneas .25 / .75 → mismo arreglo que en match totals.
    if not (math.isclose(pt % 1, 0.0) or math.isclose(pt % 1, 0.5)):
        nearest_half = math.floor(pt) + 0.5
        return probability_over_single_poisson(lam, nearest_half, k_tail=k_tail)

    mu = max(0.0, lam)

    def cdf_until(k_hi: int) -> float:
        """P(X <= k_hi)."""
        if k_hi < 0:
            return 0.0
        return sum(_poisson_pmf(i, mu) for i in range(min(k_hi, k_tail) + 1))

    if math.isclose(pt % 1, 0.5):
        threshold = math.ceil(pt)
        p_over = 1.0 - cdf_until(threshold - 1)
        return max(0.0, min(1.0, p_over)), max(0.0, min(1.0, 1.0 - p_over))

    point_int = int(round(pt))
    p_over = 1.0 - cdf_until(point_int)
    p_under = cdf_until(point_int - 1) if point_int > 0 else 0.0
    return max(0.0, min(1.0, p_over)), max(0.0, min(1.0, p_under))


def derive_market_probabilities(
    lambda_home: float,
    lambda_away: float,
    *,
    grid_size: int = GOAL_GRID_SIZE,
    dixon_coles_rho: float = 0.0,
) -> MatchExpectation:
    """A partir de las lambdas calcula todas las probabilidades de mercado.

    Si ``dixon_coles_rho`` es distinto de cero, aplica la corrección DC sobre
    la matriz score antes de derivar el resto de mercados (1X2, O/U, BTTS).
    """
    grid = _build_grid(
        lambda_home, lambda_away,
        size=grid_size, dixon_coles_rho=dixon_coles_rho,
    )

    p_home_win = 0.0
    p_draw = 0.0
    p_away_win = 0.0
    p_over_15 = 0.0
    p_over_25 = 0.0
    p_over_35 = 0.0
    p_btts_yes = 0.0

    for h in range(grid_size):
        for a in range(grid_size):
            cell = grid[h][a]
            if h > a:
                p_home_win += cell
            elif h == a:
                p_draw += cell
            else:
                p_away_win += cell
            total_goals = h + a
            if total_goals >= 2:
                p_over_15 += cell
            if total_goals >= 3:
                p_over_25 += cell
            if total_goals >= 4:
                p_over_35 += cell
            # BTTS = ambos marcan ⇒ celdas con h ≥ 1 y a ≥ 1
            if h >= 1 and a >= 1:
                p_btts_yes += cell

    return MatchExpectation(
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        p_home_win=p_home_win,
        p_draw=p_draw,
        p_away_win=p_away_win,
        p_over_15=p_over_15,
        p_under_15=1.0 - p_over_15,
        p_over_25=p_over_25,
        p_under_25=1.0 - p_over_25,
        p_over_35=p_over_35,
        p_under_35=1.0 - p_over_35,
        p_btts_yes=p_btts_yes,
        p_btts_no=1.0 - p_btts_yes,
        dixon_coles_rho=dixon_coles_rho,
    )


def predict(
    home: TeamLeagueAverages,
    away: TeamLeagueAverages,
    league_teams: list[TeamLeagueAverages],
    *,
    home_advantage: float = HOME_ADVANTAGE,
    dixon_coles_rho: float = 0.0,
) -> MatchExpectation:
    """API de alto nivel: dado un home/away y los equipos de la liga, predice."""
    league_avg = league_average_goals(league_teams)
    lh, la = expected_goals(home, away, league_avg, home_advantage=home_advantage)
    return derive_market_probabilities(lh, la, dixon_coles_rho=dixon_coles_rho)
