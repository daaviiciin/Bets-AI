"""Detector de errores y discrepancias de cuotas entre casas (bet365 ↔ Winamax). Puro.

Sigue el algoritmo de ``INVESTIGACION_ERRORES_CUOTAS.md`` §6.3, por partido:

1. **Frescura:** fuera las cuotas con más de ``max_antiguedad``; un par bet365/Winamax solo se
   compara si se leyeron con menos de ``max_desfase_par`` de diferencia.
2. **Quitar el margen** de cada casa en cada mercado completo (método *power*,
   ``sin_margen_power``): 1X2 y hándicap europeo (3 resultados); más/menos, ambos marcan,
   par/impar, empate no válido y hándicap asiático (2). La doble oportunidad se deriva del 1X2
   de la misma casa. Marcador exacto y descanso/final no son completos: se comparan con la
   mediana de las cuotas de las demás casas, sin quitar margen.
3. **Referencia:** casa ``sharp`` si la hay (hoy ninguna: el comparador de Flashscore en
   España no trae Pinnacle ni Betfair) → **consenso** (mediana sin margen de ≥ 3 casas
   distintas de la evaluada) → solo **par** (bet365 frente a Winamax).
4. **Ventaja** ``edge = cuota × P_ref − 1`` y **z robusta** del par ``ln(o_W / o_B)``, con MAD
   por defecto 0,04 mientras no haya historial para calibrarla.
5. **Surebets** con las mejores cuotas de cada resultado: ``margen = 1 − Σ 1/max(cuota)``.
6. **Clasificación** (:func:`clasificar_discrepancia`) en error probable / surebet / cuota
   desfasada / value / ruido, con su confianza.

Las cuotas entran en **claves neutrales** (las de ``comparador_cuotas``: ``home``, ``draw``,
``away``, ``over``, ``under``, ``yes``, ``no``, ``1X``, ``12``, ``X2``, ``odd``, ``even``…; la
línea de los hándicaps, desde el local). Las de Winamax se traducen antes con
``errores_cuotas.lado_neutral_winamax``. Las cuentas internas van en ``float`` (como
``sin_margen_power``) y lo que sale, en ``Decimal``.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from backend.services.market_probabilities import sin_margen_power
from backend.services.winamax_normalization import TOTALS_MARKETS

BET365 = "bet365_es"
WINAMAX = "winamax_es"

ROL_SHARP = "sharp"
REF_SHARP = "sharp"
REF_CONSENSO = "consensus"
REF_PAR = "pair"
REF_NINGUNA = "none"


class Clase(StrEnum):
    ERROR_PROBABLE = "error_probable"
    SUREBET = "surebet"
    DESFASADA = "desfasada"
    VALUE = "value"
    RUIDO = "ruido"


class Confianza(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


@dataclass(frozen=True)
class CuotaObservada:
    """Cuota vigente de una casa en una línea de un partido."""

    casa: str  # clave de ``bookmakers`` (``bet365_es``, ``winamax_es``…)
    market_key: str
    outcome: str  # clave neutral
    line: Decimal | None
    price: Decimal
    seen_at: datetime  # última vez que se vio este precio
    #: Desde cuándo la casa tiene este precio (``first_seen_at`` de la fila vigente).
    desde: datetime | None = None
    #: Precio anterior de la misma línea (la fila previa del historial), si lo hay.
    precio_anterior: Decimal | None = None
    rol: str = "soft"


@dataclass(frozen=True)
class Parametros:
    """Umbrales del detector. Son los **iniciales** del documento: se ajustan con el registro."""

    casas_objetivo: tuple[str, ...] = (BET365, WINAMAX)
    max_antiguedad: timedelta = timedelta(hours=6)
    max_desfase_par: timedelta = timedelta(minutes=30)
    min_casas_consenso: int = 3
    mad_por_defecto: float = 0.04


@dataclass(frozen=True)
class Senales:
    """Lo que :func:`clasificar_discrepancia` sabe de una línea de una casa."""

    #: ``cuota × P_ref − 1``; ``None`` si no hay referencia sin margen.
    edge: float | None
    #: Cuota de la casa / cuota justa de la referencia (= ``edge + 1``). En los mercados no
    #: completos, cuota / mediana de las cuotas de las demás casas (con margen).
    ratio_ref: float | None
    #: z robusta de ``ln(o_W / o_B)`` (positiva = esta casa paga más que la otra objetivo).
    z_par: float | None
    #: ``sharp`` / ``consensus`` / ``pair`` / ``none``.
    ref_kind: str
    #: La cuota coincide con la del **otro** lado en el resto de casas (lados cambiados).
    lado_cambiado: bool = False
    #: Margen de la surebet en la que esta cuota es la mejor de su resultado (≥ 0), o ``None``.
    surebet_margen: float | None = None
    #: Cuánto se movió la P de referencia desde la lectura anterior (en tanto por uno, abs.).
    ref_movimiento: float | None = None
    #: La casa no ha cambiado su precio desde antes de que se moviera la referencia.
    casa_sin_cambios: bool = False
    #: Minutos entre esta cuota y la de la otra casa objetivo (o la más vieja de la surebet).
    desfase_min: float | None = None
    #: Antigüedad de esta cuota en minutos.
    antiguedad_min: float = 0.0


def clasificar_discrepancia(s: Senales) -> tuple[Clase, Confianza | None]:
    """Clase y confianza de una discrepancia. **Primera regla que se cumpla.**

    Criterios posibles (``INVESTIGACION_ERRORES_CUOTAS.md`` §6.3, tabla del paso 7):

    * **Error probable:** ``ratio_ref ≥ 1,5`` (o ``≤ 0,67``), o ``|z_par| ≥ 6``, o
      ``lado_cambiado``. Confianza alta si es por lados cambiados; media si solo es por tamaño.
      Es lo que Winamax anula por «error manifiesto» y bet365 recalcula: no se recomienda.
    * **Surebet:** ``surebet_margen ≥ 1 %`` y no es error probable. Alta si las cuotas se
      leyeron con ≤ 2 min de diferencia, media si ≤ 10, baja si más.
    * **Cuota desfasada:** la referencia se movió ≥ 4 % (``ref_movimiento``), la casa no
      (``casa_sin_cambios``) y ``edge ≥ 2 %``. Alta con referencia ``sharp``, si no media.
    * **Value:** ``edge ≥ 3 %`` con ``sharp``, ``≥ 5 %`` con consenso, ``≥ 8 %`` con modelo; y,
      si hay par, ``2 ≤ z < 6``. Confianza alta / media / baja según la referencia.
    * **Ruido:** todo lo demás (confianza ``None``).

    Otras ideas que se pueden probar: exigir menos ventaja cuanto más fresca sea la cuota
    (``antiguedad_min``), endurecer los umbrales en cuotas altas (> 5, donde la dispersión
    entre casas es mayor) o pedir que bet365 y el consenso coincidan antes de marcar a Winamax.
    """
    # TODO(usuario): cuerpo provisional con los umbrales iniciales del documento. Ajústalo
    # (o cámbialo por otro criterio) cuando el registro en seco diga qué separa el ruido de
    # las discrepancias que se pagan.
    ratio = s.ratio_ref
    z = s.z_par
    if s.lado_cambiado:
        return Clase.ERROR_PROBABLE, Confianza.ALTA
    if (ratio is not None and (ratio >= 1.5 or ratio <= 0.67)) or (z is not None and abs(z) >= 6):
        return Clase.ERROR_PROBABLE, Confianza.MEDIA
    if s.surebet_margen is not None and s.surebet_margen >= 0.01:
        d = s.desfase_min if s.desfase_min is not None else 0.0
        conf = Confianza.ALTA if d <= 2 else Confianza.MEDIA if d <= 10 else Confianza.BAJA
        return Clase.SUREBET, conf
    edge = s.edge
    if (
        edge is not None and edge >= 0.02 and s.casa_sin_cambios
        and s.ref_movimiento is not None and s.ref_movimiento >= 0.04
    ):
        return Clase.DESFASADA, Confianza.ALTA if s.ref_kind == REF_SHARP else Confianza.MEDIA
    umbral = {REF_SHARP: 0.03, REF_CONSENSO: 0.05, "model": 0.08}.get(s.ref_kind)
    if edge is not None and umbral is not None and edge >= umbral:
        if z is None or 2 <= z < 6:
            conf = {REF_SHARP: Confianza.ALTA, REF_CONSENSO: Confianza.MEDIA}.get(
                s.ref_kind, Confianza.BAJA,
            )
            return Clase.VALUE, conf
    return Clase.RUIDO, None


@dataclass(frozen=True)
class PataSurebet:
    outcome: str
    casa: str
    precio: Decimal


@dataclass(frozen=True)
class Surebet:
    market_key: str
    line: Decimal | None
    margen: Decimal
    patas: tuple[PataSurebet, ...]
    desfase_min: Decimal


@dataclass(frozen=True)
class Discrepancia:
    """Una línea de una casa objetivo que no es ruido."""

    market_key: str
    outcome: str
    line: Decimal | None
    casa: str
    precio: Decimal
    seen_at: datetime
    antiguedad_min: Decimal
    casa_otra: str | None
    precio_otra: Decimal | None
    seen_at_otra: datetime | None
    p_ref: Decimal | None
    cuota_justa: Decimal | None
    ref_kind: str
    n_casas_ref: int
    edge: Decimal | None
    z: Decimal | None
    clase: Clase
    confianza: Confianza | None
    motivo: str
    surebet: Surebet | None = None


@dataclass
class ResultadoDeteccion:
    discrepancias: list[Discrepancia] = field(default_factory=list)
    surebets: list[Surebet] = field(default_factory=list)
    lineas_comparadas: int = 0


# ---------------------------------------------------------------------------
# Mercados completos
# ---------------------------------------------------------------------------

_TRES = ("home", "draw", "away")


def resultados_del_mercado(market_key: str) -> tuple[str, ...] | None:
    """Resultados que forman el mercado completo, o ``None`` si no se puede quitar el margen."""
    if market_key.endswith("h2h") or market_key.endswith("european_handicap"):
        return _TRES
    if market_key in TOTALS_MARKETS or market_key.endswith("totals"):
        return ("over", "under")
    if market_key.endswith("btts"):
        return ("yes", "no")
    if market_key.endswith("odd_even"):
        return ("odd", "even")
    if market_key.endswith("draw_no_bet") or market_key.endswith("asian_handicap"):
        return ("home", "away")
    return None


_DOBLE = {"1X": ("home", "draw"), "12": ("home", "away"), "X2": ("draw", "away")}
_OTRO_LADO = {
    "over": "under", "under": "over", "yes": "no", "no": "yes", "odd": "even", "even": "odd",
    "home": "away", "away": "home",
}


def _f(x: Decimal | None) -> float | None:
    return float(x) if x is not None else None


def _d(x: float | None, q: str = "0.0001") -> Decimal | None:
    if x is None or math.isnan(x) or math.isinf(x):
        return None
    return Decimal(str(x)).quantize(Decimal(q))


def _minutos(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 60.0


LineaClave = tuple[str, str, Decimal | None]  # (market_key, outcome, line)


def _norm_line(line: Decimal | None) -> Decimal | None:
    return line.normalize() if line is not None else None


def _justas_por_casa(
    cuotas: list[CuotaObservada], *, usar_anterior: bool = False,
) -> dict[str, dict[LineaClave, float]]:
    """P sin margen de cada línea en cada casa (solo mercados completos y doble oportunidad)."""
    por_casa: dict[str, dict[tuple[str, Decimal | None], dict[str, float]]] = {}
    for c in cuotas:
        precio = c.precio_anterior if usar_anterior and c.precio_anterior else c.price
        if precio is None or precio <= 1:
            continue
        grupo = por_casa.setdefault(c.casa, {}).setdefault((c.market_key, _norm_line(c.line)), {})
        grupo[c.outcome] = float(precio)
    out: dict[str, dict[LineaClave, float]] = {}
    for casa, grupos in por_casa.items():
        justas: dict[LineaClave, float] = {}
        for (mk, line), sel in grupos.items():
            res = resultados_del_mercado(mk)
            if res is None or not all(r in sel for r in res):
                continue
            for r, p in zip(res, sin_margen_power([sel[r] for r in res])):
                justas[(mk, r, line)] = p
            if mk.endswith("h2h"):
                dc = mk.removesuffix("h2h") + "double_chance"
                for oc, lados in _DOBLE.items():
                    justas[(dc, oc, line)] = sum(justas[(mk, lado, line)] for lado in lados)
        out[casa] = justas
    return out


def _mediana(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def detectar(
    cuotas: list[CuotaObservada], *, now: datetime, params: Parametros | None = None,
) -> ResultadoDeteccion:
    """Discrepancias y surebets de **un partido**."""
    p = params or Parametros()
    frescas = [c for c in cuotas if now - c.seen_at <= p.max_antiguedad and c.price > 1]
    res = ResultadoDeteccion()
    if not frescas:
        return res

    por_linea: dict[LineaClave, dict[str, CuotaObservada]] = {}
    for c in frescas:
        por_linea.setdefault((c.market_key, c.outcome, _norm_line(c.line)), {})[c.casa] = c
    justas = _justas_por_casa(frescas)
    justas_ant = _justas_por_casa(frescas, usar_anterior=True)
    sharps = {c.casa for c in frescas if c.rol == ROL_SHARP}

    surebets = _surebets(frescas, p)
    res.surebets = [s for s in surebets if any(pt.casa in p.casas_objetivo for pt in s.patas)]
    mejor_en_surebet: dict[tuple[LineaClave, str], Surebet] = {}
    for s in res.surebets:
        for pt in s.patas:
            mejor_en_surebet[((s.market_key, pt.outcome, _norm_line(s.line)), pt.casa)] = s

    escala_z = 1.4826 * p.mad_por_defecto
    for clave, casas in por_linea.items():
        mk, outcome, line = clave
        for casa in p.casas_objetivo:
            x = casas.get(casa)
            if x is None:
                continue
            res.lineas_comparadas += 1
            otra_casa = next((o for o in p.casas_objetivo if o != casa and o in casas), None)
            otra = casas.get(otra_casa) if otra_casa else None
            if otra is not None and _minutos(x.seen_at, otra.seen_at) > p.max_desfase_par.total_seconds() / 60:
                otra = None

            # Referencia
            p_ref: float | None = None
            p_ref_ant: float | None = None
            ref_kind = REF_NINGUNA
            n_ref = 0
            ratio: float | None = None
            sharp_ps = [justas[s][clave] for s in sharps if s != casa and clave in justas.get(s, {})]
            cons = [j[clave] for k, j in justas.items() if k != casa and clave in j]
            if sharp_ps:
                p_ref, ref_kind, n_ref = statistics.median(sharp_ps), REF_SHARP, len(sharp_ps)
            elif len(cons) >= p.min_casas_consenso:
                p_ref, ref_kind, n_ref = statistics.median(cons), REF_CONSENSO, len(cons)
                ant = [j[clave] for k, j in justas_ant.items() if k != casa and clave in j]
                p_ref_ant = _mediana(ant)
            elif otra is not None:
                ref_kind, n_ref = REF_PAR, 1
            edge = float(x.price) * p_ref - 1 if p_ref else None
            if edge is not None:
                ratio = edge + 1
            else:
                crudas = [float(o.price) for k, o in casas.items() if k != casa]
                if len(crudas) >= p.min_casas_consenso:
                    ratio = float(x.price) / statistics.median(crudas)
                    n_ref = max(n_ref, len(crudas))
                    if ref_kind == REF_NINGUNA:
                        ref_kind = REF_CONSENSO

            z = None
            if otra is not None:
                w, b = (x, otra) if casa == WINAMAX else (otra, x)
                d = math.log(float(w.price) / float(b.price))
                z = d / escala_z if casa == WINAMAX else -d / escala_z

            senales = Senales(
                edge=edge,
                ratio_ref=ratio,
                z_par=z,
                ref_kind=ref_kind,
                lado_cambiado=_lado_cambiado(x, casas, por_linea, clave, p),
                surebet_margen=(
                    float(mejor_en_surebet[(clave, casa)].margen)
                    if (clave, casa) in mejor_en_surebet else None
                ),
                ref_movimiento=(
                    abs(p_ref - p_ref_ant) / p_ref_ant if p_ref and p_ref_ant else None
                ),
                casa_sin_cambios=_casa_sin_cambios(x, casas),
                desfase_min=(
                    _minutos(x.seen_at, otra.seen_at) if otra is not None
                    else float(mejor_en_surebet[(clave, casa)].desfase_min)
                    if (clave, casa) in mejor_en_surebet else None
                ),
                antiguedad_min=_minutos(now, x.seen_at),
            )
            clase, confianza = clasificar_discrepancia(senales)
            if clase == Clase.RUIDO:
                continue
            res.discrepancias.append(Discrepancia(
                market_key=mk, outcome=outcome, line=line, casa=casa, precio=x.price,
                seen_at=x.seen_at, antiguedad_min=_d(senales.antiguedad_min, "0.1"),
                casa_otra=otra_casa if otra is not None else None,
                precio_otra=otra.price if otra is not None else None,
                seen_at_otra=otra.seen_at if otra is not None else None,
                p_ref=_d(p_ref), cuota_justa=_d(1 / p_ref, "0.001") if p_ref else None,
                ref_kind=ref_kind, n_casas_ref=n_ref, edge=_d(edge), z=_d(z, "0.01"),
                clase=clase, confianza=confianza, motivo=_motivo(clase, senales),
                surebet=mejor_en_surebet.get((clave, casa)),
            ))
    return res


def _lado_cambiado(
    x: CuotaObservada,
    casas: dict[str, CuotaObservada],
    por_linea: dict[LineaClave, dict[str, CuotaObservada]],
    clave: LineaClave,
    p: Parametros,
) -> bool:
    """La cuota de ``x`` es la del **otro** lado en el resto de casas (y no la de su lado)."""
    mk, outcome, line = clave
    otro = _OTRO_LADO.get(outcome)
    if otro is None or resultados_del_mercado(mk) is None:
        return False
    propias = [float(o.price) for k, o in casas.items() if k != x.casa]
    ajenas = [float(o.price) for k, o in por_linea.get((mk, otro, line), {}).items() if k != x.casa]
    if len(propias) < p.min_casas_consenso or len(ajenas) < p.min_casas_consenso:
        return False
    ref_propia, ref_ajena = statistics.median(propias), statistics.median(ajenas)
    precio = float(x.price)
    return abs(precio / ref_ajena - 1) < 0.03 and abs(precio / ref_propia - 1) > 0.30


def _casa_sin_cambios(x: CuotaObservada, casas: dict[str, CuotaObservada]) -> bool:
    """Las demás casas cambiaron su precio después de la última vez que lo cambió ``x``."""
    if x.desde is None:
        return False
    cambios = [
        o.desde for k, o in casas.items()
        if k != x.casa and o.desde is not None and o.precio_anterior is not None
    ]
    return bool(cambios) and x.desde < min(cambios)


def _surebets(cuotas: list[CuotaObservada], p: Parametros) -> list[Surebet]:
    grupos: dict[tuple[str, Decimal | None], dict[str, list[CuotaObservada]]] = {}
    for c in cuotas:
        if resultados_del_mercado(c.market_key) is None:
            continue
        grupos.setdefault((c.market_key, _norm_line(c.line)), {}).setdefault(c.outcome, []).append(c)
    out: list[Surebet] = []
    for (mk, line), sel in grupos.items():
        res = resultados_del_mercado(mk) or ()
        if not all(r in sel for r in res):
            continue
        mejores = [max(sel[r], key=lambda c: c.price) for r in res]
        suma = sum(1 / float(c.price) for c in mejores)
        if suma >= 1:
            continue
        tiempos = [c.seen_at for c in mejores]
        out.append(Surebet(
            market_key=mk, line=line, margen=_d(1 - suma),
            patas=tuple(PataSurebet(r, c.casa, c.price) for r, c in zip(res, mejores)),
            desfase_min=_d((max(tiempos) - min(tiempos)).total_seconds() / 60, "0.1"),
        ))
    return out


def _motivo(clase: Clase, s: Senales) -> str:
    """Explicación corta para la pantalla."""
    partes: list[str] = []
    if s.lado_cambiado:
        partes.append("la cuota coincide con la del otro lado en el resto de casas")
    if s.ratio_ref is not None:
        partes.append(f"{(s.ratio_ref - 1) * 100:+.1f} % frente a la referencia")
    if s.z_par is not None:
        partes.append(f"z = {s.z_par:.1f} frente a la otra casa")
    if s.surebet_margen is not None:
        partes.append(f"surebet del {s.surebet_margen * 100:.1f} %")
    if clase == Clase.DESFASADA and s.ref_movimiento is not None:
        partes.append(f"el mercado se movió un {s.ref_movimiento * 100:.0f} % y esta casa no")
    return "; ".join(partes)
