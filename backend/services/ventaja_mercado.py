"""Ventaja de las cuotas de Winamax frente al resto del mercado (comparador de Flashscore).

Para cada línea de Winamax que también está en el comparador, la **referencia** es el
consenso sin margen de las demás casas españolas (mediana de ≥ 3 casas; hoy no hay Pinnacle
ni Betfair) y la **ventaja** es ``cuota de Winamax × P_consenso − 1``, el EV frente al mercado.
Se marca con el baremo del usuario (2026-11-18): *value* de 3 a 15 %; por encima, error
probable (Winamax puede anular la apuesta). La clasificación es la de
:func:`backend.services.odds_discrepancies.clasificar_discrepancia`.

La cuota de Winamax es la de nuestro scraper (la que enseña la app), no la que trae el
comparador para Winamax.es. Las del resto de casas son las de la **última lectura** del
comparador de cada partido (todas las casas de una lectura comparten ``last_seen_at``). Si
Winamax se leyó a más de ``max_desfase_consenso`` (2 h) de esa lectura, no se compara.

Es una medida frente a casas blandas, no frente a una referencia afilada: no dice cuál de las
dos se equivoca (``INVESTIGACION_ERRORES_CUOTAS.md`` §0.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.models import Bookmaker, ComparadorCuota, Market
from backend.services import odds_discrepancies as od

#: ``(market_key, outcome_name, outcome_point)`` de una línea de ``odds`` (con nombres de equipo).
ClaveWinamax = tuple[str, str, Decimal | None]


@dataclass(frozen=True)
class LineaWinamax:
    """Una cuota vigente de Winamax (de nuestro scraper)."""

    market_key: str
    outcome_name: str
    outcome_point: Decimal | None
    price: Decimal
    captured_at: datetime


@dataclass(frozen=True)
class VentajaMercado:
    """Lo que se enseña de una línea de Winamax que no es ruido frente al mercado."""

    #: ``value`` / ``surebet`` / ``desfasada`` / ``error_probable``.
    clase: str
    #: ``cuota × P_consenso − 1`` (0,062 = Winamax paga un 6,2 % por encima de la cuota justa).
    ventaja: Decimal
    #: P sin margen del consenso de las demás casas.
    p_mercado: Decimal
    cuota_justa: Decimal
    #: Casas que forman el consenso.
    casas: int
    #: Hora de la lectura del comparador.
    leido_at: datetime


def ventajas_del_partido(
    winamax: Iterable[LineaWinamax],
    otras: Iterable[od.CuotaObservada],
    *,
    home: str,
    away: str,
    now: datetime,
    params: od.Parametros | None = None,
) -> dict[ClaveWinamax, VentajaMercado]:
    """Líneas de Winamax de **un partido** que el detector marca (y que pagan más que el
    mercado). Puro: ``otras`` son las cuotas de las demás casas, ya en claves neutrales."""
    obs = [c for c in otras if c.casa != od.WINAMAX]
    if not obs:
        return {}
    original: dict[od.LineaClave, ClaveWinamax] = {}
    precio: dict[od.LineaClave, LineaWinamax] = {}
    dudosas: set[od.LineaClave] = set()
    for w in winamax:
        k = od.clave_neutral_winamax(w.market_key, w.outcome_name, w.outcome_point, home, away)
        if k is None:
            continue
        if k in precio:
            # Dos selecciones con la misma clave y distinto precio (un nombre mal casado al
            # guardar, p. ej. dos «Deportivo Alavés» en el 1X2): no se sabe cuál es cuál.
            if precio[k].price != w.price:
                dudosas.add(k)
            continue
        precio[k] = w
        original[k] = (w.market_key, w.outcome_name, w.outcome_point)
    for k, w in precio.items():
        if k in dudosas:
            continue
        mk, oc, line = k
        obs.append(od.CuotaObservada(
            casa=od.WINAMAX, market_key=mk, outcome=oc, line=line, price=w.price,
            seen_at=w.captured_at,
        ))
    if len(dudosas) == len(precio):
        return {}
    leido = max(c.seen_at for c in obs if c.casa != od.WINAMAX)
    res = od.detectar(obs, now=now, params=params or od.Parametros(casas_objetivo=(od.WINAMAX,)))
    out: dict[ClaveWinamax, VentajaMercado] = {}
    for d in res.discrepancias:
        clave = original.get((d.market_key, d.outcome, d.line))
        # Solo interesa lo que Winamax paga **por encima** del mercado, con referencia sin margen.
        if clave is None or d.edge is None or d.edge <= 0 or d.p_ref is None or d.cuota_justa is None:
            continue
        out[clave] = VentajaMercado(
            clase=str(d.clase), ventaja=d.edge, p_mercado=d.p_ref, cuota_justa=d.cuota_justa,
            casas=d.n_casas_ref, leido_at=leido,
        )
    return out


def cuotas_comparador_vigentes(
    session: Session, match_ids: Iterable[int],
) -> dict[int, list[od.CuotaObservada]]:
    """Cuotas de la última lectura del comparador de cada partido, salvo las de Winamax.es."""
    ids = sorted(set(match_ids))
    if not ids:
        return {}
    ultima = (
        select(
            ComparadorCuota.match_id.label("match_id"),
            func.max(ComparadorCuota.last_seen_at).label("visto"),
        )
        .where(ComparadorCuota.match_id.in_(ids))
        .group_by(ComparadorCuota.match_id)
        .subquery()
    )
    filas = session.execute(
        select(
            ComparadorCuota.match_id, Bookmaker.key, Bookmaker.role, Market.key,
            ComparadorCuota.outcome_name, ComparadorCuota.outcome_point, ComparadorCuota.price,
            ComparadorCuota.first_seen_at, ComparadorCuota.last_seen_at,
        )
        .join(ultima, and_(
            ultima.c.match_id == ComparadorCuota.match_id,
            ultima.c.visto == ComparadorCuota.last_seen_at,
        ))
        .join(Bookmaker, Bookmaker.id == ComparadorCuota.bookmaker_id)
        .join(Market, Market.id == ComparadorCuota.market_id)
        .where(Bookmaker.key != od.WINAMAX),
    ).all()
    out: dict[int, list[od.CuotaObservada]] = {}
    for mid, casa, rol, mk, oc, pt, price, desde, visto in filas:
        out.setdefault(mid, []).append(od.CuotaObservada(
            casa=casa, market_key=mk, outcome=oc, line=pt, price=price, seen_at=visto,
            desde=desde, rol=rol or "soft",
        ))
    return out


__all__ = [
    "ClaveWinamax",
    "LineaWinamax",
    "VentajaMercado",
    "cuotas_comparador_vigentes",
    "ventajas_del_partido",
]
