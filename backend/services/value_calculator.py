"""Cálculo de valor de apuestas (probabilidad implícita, EV, Kelly).

Todas las funciones son **puras** (sin efectos secundarios) y usan ``Decimal``
para evitar errores de precisión en cálculos financieros.

Conceptos clave
---------------

**Probabilidad implícita** de una cuota: ``1 / cuota``. Si la cuota es 2.00,
el bookie te dice que esa probabilidad es del 50 %.

**Margen (vig)** de un bookmaker: cuánto se queda el bookie por encima del
100 %. Si las cuotas implícitas suman 1.07, el margen es del 7 %.

**Probabilidad real (fair)**: probabilidad implícita "limpia" tras quitarle
el margen. Es nuestra mejor estimación de la probabilidad verdadera del
suceso (especialmente si la calculamos a partir de un bookie afilado como
Pinnacle o 1xBet).

**EV (Expected Value)**: ``EV = cuota_ofrecida * prob_real - 1``. Si es > 0,
matemáticamente es una apuesta de valor.

**Criterio de Kelly**: fracción óptima del bankroll a apostar para maximizar
el crecimiento logarítmico esperado. El Kelly puro es agresivo; los
profesionales usan Kelly fraccional (1/2, 1/4) según su tolerancia al riesgo.

**Cuota recomendable**: solo se recomienda (value bet, recomendación, combinada,
pick destacado, marca de valor) una cuota ``<= MAX_RECOMMENDABLE_ODDS``. Las
cuotas por encima se guardan y se muestran, pero son solo informativas.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, getcontext

getcontext().prec = 28

ZERO = Decimal("0")
ONE = Decimal("1")

# Regla de dominio (no es configuración): por encima de esta cuota la línea es informativa.
# Fuente única para el detector de value bets, recomendaciones, combinadas y graded odds.
MAX_RECOMMENDABLE_ODDS = Decimal("4.00")


def is_recommendable_odds(odds: Decimal | float | int | str | None) -> bool:
    """``True`` si la cuota es válida (> 1) y no supera ``MAX_RECOMMENDABLE_ODDS`` (4,00 incluida)."""
    if odds is None:
        return False
    odds = _ensure_decimal(odds)
    if not odds.is_finite():
        return False
    return ONE < odds <= MAX_RECOMMENDABLE_ODDS


def implied_probability(odds: Decimal) -> Decimal:
    """Probabilidad implícita (con vig incluido) de una cuota decimal.

    Ejemplo: ``implied_probability(2.00) == 0.5``.
    """
    odds = _ensure_decimal(odds)
    if odds <= ONE:
        raise ValueError(f"La cuota debe ser > 1.0 (recibida: {odds})")
    return ONE / odds


def bookmaker_margin(odds: Iterable[Decimal]) -> Decimal:
    """Margen total ("vig" / "overround") de un set completo de cuotas.

    Para un mercado bien formado (1X2, Over/Under), las probabilidades
    implícitas de TODAS las opciones suman > 1; ese exceso es el margen.

    Ejemplo: cuotas 1.50 / 4.00 / 7.00 → suma 0.916 → margen = -8.4 % (?!)
    pero realmente: 1/1.50 + 1/4.00 + 1/7.00 = 0.667 + 0.25 + 0.143 = 1.06
    → margen 6 %.
    """
    total = sum((implied_probability(o) for o in odds), start=ZERO)
    return total - ONE


def fair_probabilities(odds_by_outcome: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Quita el vig proporcionalmente y devuelve probabilidades "limpias".

    Asume que el margen está repartido **proporcionalmente** entre todas las
    opciones, lo cual es una aproximación habitual. Existen métodos más finos
    (Shin, log-power) pero la diferencia es pequeña en mercados líquidos.
    """
    if not odds_by_outcome:
        raise ValueError("odds_by_outcome no puede estar vacío")

    implied = {k: implied_probability(v) for k, v in odds_by_outcome.items()}
    total = sum(implied.values(), start=ZERO)
    if total <= ZERO:
        raise ValueError("Probabilidades implícitas no positivas")
    return {k: p / total for k, p in implied.items()}


def expected_value(offered_odds: Decimal, fair_prob: Decimal) -> Decimal:
    """Valor esperado de la apuesta. Positivo = apuesta con valor.

    ``EV = cuota * prob_real - 1``

    Un EV de 0.10 significa que esperas ganar el 10 % de tu stake a largo
    plazo si la prob_real es correcta.
    """
    offered_odds = _ensure_decimal(offered_odds)
    fair_prob = _ensure_decimal(fair_prob)
    if not (ZERO <= fair_prob <= ONE):
        raise ValueError(f"fair_prob fuera de rango [0, 1]: {fair_prob}")
    return offered_odds * fair_prob - ONE


def kelly_fraction(offered_odds: Decimal, fair_prob: Decimal) -> Decimal:
    """Fracción del bankroll a apostar según Kelly puro.

    ``f* = (b * p - q) / b`` donde ``b = cuota - 1`` y ``q = 1 - p``.

    Devuelve 0 si la apuesta no tiene valor (en lugar de un número negativo,
    porque "apostar negativo" no tiene sentido práctico).
    """
    offered_odds = _ensure_decimal(offered_odds)
    fair_prob = _ensure_decimal(fair_prob)
    if offered_odds <= ONE:
        raise ValueError(f"La cuota debe ser > 1.0 (recibida: {offered_odds})")

    b = offered_odds - ONE
    q = ONE - fair_prob
    f = (b * fair_prob - q) / b
    return max(f, ZERO)


def recommended_stake(
    bankroll: Decimal,
    offered_odds: Decimal,
    fair_prob: Decimal,
    *,
    kelly_modifier: Decimal = ONE,
    max_stake_pct: Decimal | None = None,
    flat_stake: Decimal | None = None,
) -> Decimal:
    """Stake recomendado para una apuesta concreta.

    Parámetros
    ----------
    bankroll
        Dinero total disponible para apostar.
    offered_odds, fair_prob
        Cuota del bookie y probabilidad real estimada.
    kelly_modifier
        Multiplicador del Kelly (1.0 puro, 0.5 medio Kelly, 0.25 cuarto).
    max_stake_pct
        Tope superior como fracción del bankroll (ej. 0.05 = máx 5 %).
    flat_stake
        Si se proporciona, ignora Kelly y devuelve este stake fijo
        siempre que la apuesta tenga valor (EV > 0). Útil para perfiles
        "cazador de premios" o conservadores muy planos.
    """
    bankroll = _ensure_decimal(bankroll)
    if bankroll <= ZERO:
        return ZERO

    if expected_value(offered_odds, fair_prob) <= ZERO:
        return ZERO

    if flat_stake is not None:
        return min(_ensure_decimal(flat_stake), bankroll)

    f = kelly_fraction(offered_odds, fair_prob) * _ensure_decimal(kelly_modifier)
    stake = bankroll * f

    if max_stake_pct is not None:
        cap = bankroll * _ensure_decimal(max_stake_pct)
        stake = min(stake, cap)

    return min(stake, bankroll)


def parlay_odds_and_probability(
    legs: Iterable[tuple[Decimal, Decimal]],
) -> tuple[Decimal, Decimal]:
    """Cuota combinada y probabilidad combinada de una parlay.

    ``legs`` es un iterable de tuplas ``(cuota, prob_real)``. La cuota
    combinada es el producto de las cuotas individuales; la probabilidad
    combinada es el producto de las probabilidades individuales (asumiendo
    independencia, lo cual es razonable entre partidos distintos).
    """
    combined_odds = ONE
    combined_prob = ONE
    n = 0
    for odds, prob in legs:
        combined_odds *= _ensure_decimal(odds)
        combined_prob *= _ensure_decimal(prob)
        n += 1
    if n == 0:
        raise ValueError("Una parlay necesita al menos 1 pata")
    return combined_odds, combined_prob


def _ensure_decimal(value: Decimal | float | int | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
