"""Genera y congela los picks de hoy si aún no existen (fase 3, bloque B10).

Uso::

    python -m backend.tools.generar_picks_del_dia

Lo lanza ``refresh_programado.ps1`` justo después de descargar las cuotas de Winamax, así que
no vuelve a descargarlas. Si los picks de hoy ya existen (porque se abrió antes la app) no hace
nada: una vez generados, no cambian.
"""

from __future__ import annotations

import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.services.featured_daily_picks import asegurar_picks_de_hoy, simples_corregidas


def main() -> int:
    force_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    log = logging.getLogger("generar_picks_del_dia")
    with get_session_factory()() as session:
        res = asegurar_picks_de_hoy(session, log=log)
        if res.fila is None:
            log.info("Picks del día: no se generan (%s).", res.motivo)
            return 0
        fila = res.fila
        simples = simples_corregidas(fila)
        sin_valor = sum(bool(lg.get("sin_valor")) for lg in simples)
        combo = fila.combo_json
        meta = fila.selection_meta or {}
        log.info(
            "Picks del %s (%s, %s, generados %s): %d simples (%d sin valor), combinada %s.",
            fila.pick_date, fila.rules_version, fila.model_version,
            f"{fila.generated_at:%H:%M}" if fila.generated_at else "?",
            len(simples), sin_valor,
            f"a {combo['combined_odds']} con {len(combo['legs'])} patas" if combo else "no",
        )
        for motivo in (meta.get("motivo_simples"), meta.get("motivo_combinada")):
            if motivo:
                log.info("  %s", motivo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
