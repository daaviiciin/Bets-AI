"""Dataset del modelo v2 (fase 3): cobertura (B0) y partidos de entrenamiento sin fuga (B3).

El CLI ``backend.tools.diagnostico_modelo`` saca de la BD una fila por partido terminado
(:class:`FilaPartido`) y aquí se resumen sin tocar la BD, para poder probarlo con datos
sintéticos. :func:`load_training_matches` carga los partidos de entrenamiento con un corte
temporal obligatorio y :func:`assert_sin_fuga` comprueba que ninguno lo supera.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Claves de liga de competiciones UEFA (el resto se tratan como ligas domésticas).
UEFA_SPORT_KEYS: frozenset[str] = frozenset({
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
})


@dataclass(frozen=True)
class FilaPartido:
    """Un partido terminado con lo que se sabe de sus stats (``None`` = sin fila de stats)."""

    match_id: int
    sport_key: str
    league: str
    commence_time: datetime
    home_team_id: int
    away_team_id: int
    #: Número de lados (0, 1 o 2) con fila en ``match_stats``.
    lados_con_stats: int = 0
    #: Lados con xG / goles por mitad / tiros a puerta no nulos.
    lados_con_xg: int = 0
    lados_con_mitades: int = 0
    lados_con_sot: int = 0


@dataclass(frozen=True)
class CoberturaLiga:
    """Cobertura de stats de una liga (cuenta partidos con los **dos** lados completos)."""

    league: str
    partidos: int
    con_stats: int
    con_xg: int
    con_mitades: int
    con_sot: int

    @staticmethod
    def _pct(n: int, total: int) -> Decimal | None:
        if total <= 0:
            return None
        return (Decimal(100) * n / total).quantize(Decimal("0.1"))

    @property
    def pct_stats(self) -> Decimal | None:
        return self._pct(self.con_stats, self.partidos)

    @property
    def pct_xg(self) -> Decimal | None:
        return self._pct(self.con_xg, self.partidos)

    @property
    def pct_mitades(self) -> Decimal | None:
        return self._pct(self.con_mitades, self.partidos)

    @property
    def pct_sot(self) -> Decimal | None:
        return self._pct(self.con_sot, self.partidos)


def resumen_cobertura(filas: Iterable[FilaPartido]) -> list[CoberturaLiga]:
    """Agrupa por liga y cuenta partidos con stats completas; añade una fila ``TOTAL`` al final.

    Un partido cuenta como cubierto para un dato solo si lo tienen **los dos** equipos.
    Ligas ordenadas por nombre.
    """
    acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    for f in filas:
        a = acc[f.league]
        a[0] += 1
        a[1] += int(f.lados_con_stats >= 2)
        a[2] += int(f.lados_con_xg >= 2)
        a[3] += int(f.lados_con_mitades >= 2)
        a[4] += int(f.lados_con_sot >= 2)
    out = [CoberturaLiga(lg, *vals) for lg, vals in sorted(acc.items())]
    if out:
        tot = [sum(c) for c in zip(*(acc[k] for k in acc))]
        out.append(CoberturaLiga("TOTAL", *tot))
    return out


def equipos_liga_y_uefa(filas: Iterable[FilaPartido]) -> set[int]:
    """Ids de equipo con algún partido terminado en liga doméstica **y** alguno en UEFA."""
    liga: set[int] = set()
    uefa: set[int] = set()
    for f in filas:
        dest = uefa if f.sport_key in UEFA_SPORT_KEYS else liga
        dest.add(f.home_team_id)
        dest.add(f.away_team_id)
    return liga & uefa


def puede_ir_a_prorroga(sport_key: str, commence_time: datetime) -> bool:
    """``True`` si el partido pudo jugarse a eliminatoria (y por tanto ir a prórroga).

    No hay dato de prórroga en la BD (ver ``saved_parlay_grading``), así que es una
    heurística por calendario: las ligas domésticas nunca tienen prórroga y en UEFA la
    fase de liga va de septiembre a enero. Julio-agosto (previas) y febrero-junio
    (eliminatorias) sí pueden ir a prórroga.
    """
    if sport_key not in UEFA_SPORT_KEYS:
        return False
    return commence_time.month not in {9, 10, 11, 12, 1}


def partidos_con_posible_prorroga(filas: Iterable[FilaPartido]) -> list[FilaPartido]:
    """Partidos terminados que, por competición y fecha, podrían haber ido a prórroga."""
    return [f for f in filas if puede_ir_a_prorroga(f.sport_key, f.commence_time)]


def resumen_mercados(conteos: Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
    """Suma conteos por clave de mercado y los ordena de mayor a menor (desempate por clave)."""
    acc: dict[str, int] = defaultdict(int)
    for key, n in conteos:
        acc[key] += int(n)
    return sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))


# ---------------------------------------------------------------------------
# Partidos de entrenamiento (B3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingMatch:
    """Un partido terminado tal como lo ve el modelo v2 (marcador a 90' y lo que haya de stats)."""

    match_id: int
    sport_key: str
    commence_time: datetime
    home_id: int
    away_id: int
    home_goals: int
    away_goals: int
    #: xG de cada equipo (``None`` si falta en alguno de los dos).
    home_xg: float | None = None
    away_xg: float | None = None
    #: Goles del partido en cada mitad (``None`` si falta el dato de algún equipo).
    goals_1h: int | None = None
    goals_2h: int | None = None


def assert_sin_fuga(matches: Iterable[TrainingMatch], cutoff: datetime) -> None:
    """Falla si algún partido empezó en ``cutoff`` o después (dato del futuro)."""
    for m in matches:
        if m.commence_time >= cutoff:
            raise ValueError(
                f"Fuga de datos: el partido {m.match_id} ({m.commence_time:%Y-%m-%d %H:%M}) "
                f"no es anterior al corte {cutoff:%Y-%m-%d %H:%M}",
            )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def load_training_matches(session: Session, cutoff: datetime) -> list[TrainingMatch]:
    """Partidos de fútbol terminados con marcador y saque **anterior** a ``cutoff``.

    Quedan fuera los que pudieron ir a prórroga (eliminatorias UEFA): su marcador guardado
    incluiría la prórroga. Ordenados por fecha.
    """
    from sqlalchemy import select

    from backend.models import League, Match, MatchStats
    from backend.models.match import MATCH_STATUS_FINISHED

    filas = session.execute(
        select(
            Match.id, League.sport_key, Match.commence_time, Match.home_team_id,
            Match.away_team_id, Match.home_score, Match.away_score,
        )
        .join(League, League.id == Match.league_id)
        .where(Match.status == MATCH_STATUS_FINISHED)
        .where(Match.home_score.is_not(None), Match.away_score.is_not(None))
        .where(Match.commence_time < cutoff)
        .where(~League.sport_key.ilike("%basketball%"))
        .order_by(Match.commence_time, Match.id),
    ).all()
    filas = [f for f in filas if not puede_ir_a_prorroga(f.sport_key, _aware(f.commence_time))]
    ids = [f.id for f in filas]
    stats: dict[int, dict[bool, MatchStats]] = defaultdict(dict)
    if ids:
        for st in session.scalars(select(MatchStats).where(MatchStats.match_id.in_(ids))).all():
            stats[st.match_id][bool(st.is_home)] = st

    out: list[TrainingMatch] = []
    for f in filas:
        local, visitante = stats[f.id].get(True), stats[f.id].get(False)
        xg_ok = local is not None and visitante is not None and local.xg is not None and visitante.xg is not None
        mitades_ok = (
            local is not None and visitante is not None
            and None not in (local.goals_1h, local.goals_2h, visitante.goals_1h, visitante.goals_2h)
        )
        out.append(TrainingMatch(
            match_id=f.id,
            sport_key=f.sport_key or "",
            commence_time=_aware(f.commence_time),
            home_id=f.home_team_id,
            away_id=f.away_team_id,
            home_goals=int(f.home_score),
            away_goals=int(f.away_score),
            home_xg=float(local.xg) if xg_ok else None,
            away_xg=float(visitante.xg) if xg_ok else None,
            goals_1h=(local.goals_1h + visitante.goals_1h) if mitades_ok else None,
            goals_2h=(local.goals_2h + visitante.goals_2h) if mitades_ok else None,
        ))
    assert_sin_fuga(out, cutoff)
    return out


__all__ = [
    "UEFA_SPORT_KEYS",
    "CoberturaLiga",
    "FilaPartido",
    "TrainingMatch",
    "assert_sin_fuga",
    "equipos_liga_y_uefa",
    "load_training_matches",
    "partidos_con_posible_prorroga",
    "puede_ir_a_prorroga",
    "resumen_cobertura",
    "resumen_mercados",
]
