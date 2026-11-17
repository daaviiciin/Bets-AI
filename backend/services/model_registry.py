"""Versión activa del modelo v2 y su ajuste con los datos del momento (fase 3, B8–B9).

La versión (``model_versions``) fija hiperparámetros, calibración, peso del mercado y familias
habilitadas; los ratings se reajustan con todos los partidos terminados hasta ahora cada vez que
entra uno nuevo (el ajuste tarda centésimas). El resultado se guarda en una caché de proceso
mientras no cambien los partidos ni el día.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.models import Match, ModelVersion
from backend.models.match import MATCH_STATUS_FINISHED
from backend.models.model_version import (
    MODEL_STATUS_ACTIVE,
    MODEL_STATUS_RETIRED,
)
from backend.services.market_probabilities import MatchPrediction
from backend.services.model_calibration_v2 import Calibracion
from backend.services.model_dataset import load_training_matches
from backend.services.player_props_model import (
    ContextoProps,
    PartidoProps,
    cargar_filas_jugador,
    construir_contexto,
)
from backend.services.sot_model import FittedSot, SotHyper, fit_sot
from backend.services.team_ratings_model import FittedRatings, RatingsHyper, fit_ratings

#: Partidos mínimos de cada equipo para que el v2 dé probabilidades (si no, modelo actual).
MIN_PARTIDOS_PREDICCION = 3
#: Partidos mínimos de cada equipo para que una línea pueda ser pick.
MIN_PARTIDOS_PICK = 5

_log = logging.getLogger(__name__)

#: Props de jugador (B14c) para las versiones guardadas antes de que el backtest los midiera
#: (sin ``backtest_metrics["props"]``): familias que no salieron peor que la tasa base y su
#: calibración, del backtest del 2026-11-13. Solo entran en la combinada del partido.
PROPS_POR_DEFECTO: dict = {
    "habilitadas": ["goleador", "player_sot"],
    "calibracion": {
        "player_sot": {"a": -0.378, "b": 0.741, "n": 23934},
        "goleador": {"a": -0.444, "b": 0.819, "n": 15956},
        "paradas": {"a": 0.08, "b": 0.711, "n": 2900},
    },
}


@dataclass(frozen=True)
class ModeloActivo:
    tag: str
    fit: FittedRatings
    calibracion: Calibracion
    peso_mercado: float
    familias: frozenset[str]
    #: Tiros a puerta (B6) con los mismos partidos; ``None`` si está desactivado o faltan datos.
    sot: FittedSot | None = None
    #: Props de jugador (B14b): apariciones de cada jugador con los λ de este ajuste.
    props: ContextoProps | None = None
    #: Familias de props que pueden ir (como experimentales) en la combinada del partido.
    familias_props: frozenset[str] = frozenset()
    calibracion_props: Calibracion = Calibracion()

    def partido_props(
        self, sport_key: str, home_id: int, away_id: int, lado: str,
    ) -> PartidoProps:
        """Expectativas del partido desde el lado (``home``/``away``) del jugador."""
        pred = self.prediccion(sport_key, home_id, away_id)
        local = lado == "home"
        return PartidoProps(
            lambda_equipo=pred.lambda_home if local else pred.lambda_away,
            lambda_rival=pred.lambda_away if local else pred.lambda_home,
            sot_equipo=pred.sot_home if local else pred.sot_away,
            sot_rival=pred.sot_away if local else pred.sot_home,
            r_sot=pred.sot_r,
        )

    def prediccion(self, sport_key: str, home_id: int, away_id: int) -> MatchPrediction:
        lh, la = self.fit.lambdas(sport_key, home_id, away_id)
        sh = sa = None
        if self.sot is not None:
            sh, sa = self.sot.medias(sport_key, home_id, away_id)
        return MatchPrediction(
            lh, la, self.fit.rho, self.fit.share_2a_parte(sport_key),
            sot_home=sh, sot_away=sa, sot_r=self.sot.r if self.sot is not None else None,
        )

    def partidos_min(self, home_id: int, away_id: int) -> int:
        return min(self.fit.partidos(home_id), self.fit.partidos(away_id))


_CACHE: dict[tuple, ModeloActivo] = {}


def version_activa(session: Session) -> ModelVersion | None:
    return session.scalar(
        select(ModelVersion)
        .where(ModelVersion.status == MODEL_STATUS_ACTIVE)
        .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.id.desc())
        .limit(1),
    )


def modelo_activo(session: Session, ahora: datetime | None = None) -> ModeloActivo | None:
    """El modelo v2 activo ajustado hasta ``ahora``; ``None`` si no hay o está desactivado."""
    if not get_settings().model_v2_enabled:
        return None
    version = version_activa(session)
    if version is None:
        return None
    ahora = ahora or datetime.now(timezone.utc)
    n, ultimo = session.execute(
        select(func.count(Match.id), func.max(Match.id)).where(Match.status == MATCH_STATUS_FINISHED),
    ).one()
    firma = (version.id, version.tag, n, ultimo, ahora.date())
    if firma in _CACHE:
        return _CACHE[firma]
    matches = load_training_matches(session, ahora)
    if len(matches) < 50:
        return None
    fit = fit_ratings(
        matches, ahora, RatingsHyper(**{k: v for k, v in version.hyperparams.items() if k != "sot"}),
    )
    sot = fit_sot(matches, ahora, SotHyper(**(version.hyperparams or {}).get("sot", {})))
    props_cfg = (version.backtest_metrics or {}).get("props") or PROPS_POR_DEFECTO
    modelo = ModeloActivo(
        tag=version.tag,
        fit=fit,
        calibracion=Calibracion.from_dict(version.calibration),
        peso_mercado=float(version.market_weight),
        familias=frozenset(version.enabled_families or []),
        sot=sot,
        props=construir_contexto(
            cargar_filas_jugador(session, ahora), fit.lambdas,
            sot.medias if sot is not None else None, cutoff=ahora,
        ),
        familias_props=frozenset(props_cfg.get("habilitadas") or []),
        calibracion_props=Calibracion.from_dict(props_cfg.get("calibracion")),
    )
    _CACHE.clear()
    _CACHE[firma] = modelo
    _log.info("Modelo %s ajustado con %d partidos (ρ=%+.3f).", version.tag, len(matches), fit.rho)
    return modelo


def promover(session: Session, version: ModelVersion) -> None:
    """Deja ``version`` como la única activa (las demás activas pasan a retiradas)."""
    for v in session.scalars(select(ModelVersion).where(ModelVersion.status == MODEL_STATUS_ACTIVE)).all():
        if v.id != version.id:
            v.status = MODEL_STATUS_RETIRED
    version.status = MODEL_STATUS_ACTIVE
    version.promoted_at = datetime.now(timezone.utc)
    _CACHE.clear()


__all__ = [
    "MIN_PARTIDOS_PICK",
    "MIN_PARTIDOS_PREDICCION",
    "ModeloActivo",
    "PROPS_POR_DEFECTO",
    "modelo_activo",
    "promover",
    "version_activa",
]
