"""Backtest, calibración y promoción del modelo v2 (fase 3, bloques B7–B8).

Uso::

    python -m backend.tools.backtest_modelo              # informe y versión candidata
    python -m backend.tools.backtest_modelo --promover   # además, la activa si pasa la puerta

Pasos (todo walk-forward, sin fuga):

1. Rejilla de hiperparámetros (encogimiento σ, semivida y peso del xG); se elige con el
   log-loss de la ventana A (``--desde`` → ``--corte``).
2. Calibración Platt por familia ajustada en A y medida en B (``--corte`` → hoy).
3. Peso del mercado (cuota de cierre de Winamax sin margen) que minimiza el log-loss de los
   sucesos con cuota; el historial de cuotas empieza el 14/10, así que se elige y se mide en B.
4. Familias habilitadas para picks y puerta de promoción, siempre en B.
5. Se guarda la versión con la calibración ajustada en A+B (la que se usa en producción).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from itertools import product

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import ModelVersion
from backend.models.model_version import MODEL_STATUS_CANDIDATE
from backend.services import model_backtest as bt
from backend.services.model_dataset import load_training_matches
from backend.services.model_registry import promover
from backend.services.team_ratings_model import RatingsHyper

REJILLA = [
    RatingsHyper(sigma=s, half_life_days=hl, xg_weight=w)
    for s, hl, w in product((0.2, 0.3, 0.4, 0.55), (None, 120.0, 60.0), (1.0, 0.7, 0.4))
]


def _fecha(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _tabla(titulo: str, columnas: dict[str, dict[str, bt.Metricas]]) -> None:
    print(f"\n{titulo}")
    nombres = list(columnas)
    print(f"  {'familia':<13}{'n':>6}  " + "  ".join(f"{n:>10}" for n in nombres) + "   ECE " + nombres[0])
    familias = sorted(set().union(*(c.keys() for c in columnas.values())))
    for fam in familias:
        ref = columnas[nombres[0]].get(fam)
        celdas = [f"{c[fam].log_loss:>10.4f}" if fam in c else f"{'—':>10}" for c in columnas.values()]
        n = ref.n if ref else 0
        print(f"  {fam:<13}{n:>6}  " + "  ".join(celdas) + (f"   {ref.ece:.3f}" if ref else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest y promoción del modelo v2.")
    parser.add_argument("--desde", default="2026-09-15", help="Primer día evaluado (calentamiento antes).")
    parser.add_argument("--corte", default="2026-10-01", help="Fin de la ventana A / inicio de la B.")
    parser.add_argument("--promover", action="store_true", help="Activar la versión si pasa la puerta.")
    args = parser.parse_args(argv)
    force_utf8_stdio()
    logging.basicConfig(level=logging.WARNING)

    ahora = datetime.now(timezone.utc)
    desde, corte = _fecha(args.desde), _fecha(args.corte)
    with get_session_factory()() as session:
        matches = load_training_matches(session, ahora)
        print(f"Partidos: {len(matches)} ({matches[0].commence_time:%d/%m} → {matches[-1].commence_time:%d/%m}). "
              f"Ventana A {desde:%d/%m}–{corte:%d/%m}, B {corte:%d/%m}–{ahora:%d/%m}.")

        resultados = bt.buscar_hiper(matches, REJILLA, desde=desde, hasta_a=corte)
        print("\nRejilla (log-loss medio por familia en A, las 5 mejores):")
        for r in resultados[:5]:
            h = r.hyper
            print(f"  σ={h.sigma:<5} semivida={h.half_life_days or '∞':<6} peso goles={h.xg_weight:<4} → {r.puntuacion_a:.4f}")
        mejor = resultados[0]
        eventos = mejor.eventos
        en_a = [e for e in eventos if bt._corte(e.fecha) < corte]  # noqa: SLF001
        en_b = [e for e in eventos if bt._corte(e.fecha) >= corte]  # noqa: SLF001

        cal_a = bt.ajustar_calibracion(en_a)
        b_cal = bt.calibrar_eventos(en_b, cal_a)
        legacy_b = [e for e in bt.walk_forward_legacy(matches, desde=desde) if bt._corte(e.fecha) >= corte]  # noqa: SLF001
        v2_comun, cal_comun, legacy_comun = bt.comunes(en_b, b_cal, legacy_b)
        base_b = bt.eventos_base(matches, b_cal)
        _tabla("Ventana B, sucesos comunes con el modelo actual (log-loss):", {
            "v2 calib.": bt.por_familia(cal_comun),
            "v2 bruto": bt.por_familia(v2_comun),
            "actual": bt.por_familia(legacy_comun),
            "base": bt.por_familia(bt.eventos_base(matches, cal_comun)),
        })

        cierres = bt.cargar_cierres(session, sorted({e.match_id for e in eventos}))
        peso, curva = bt.elegir_peso_mercado(b_cal, cierres)
        mezcla_b = bt.mezclar_eventos(b_cal, cierres, peso)
        con_mercado = [e for e in b_cal if (e.match_id, e.clave) in cierres]
        if con_mercado:
            mod_m, mez_m, mer_m = bt.comunes(con_mercado, bt.mezclar_eventos(con_mercado, cierres, peso),
                                             bt.eventos_mercado(cierres, con_mercado))
            _tabla(f"Sucesos con cuota de cierre ({len(con_mercado)}), peso del mercado elegido {peso:.1f}:", {
                "mezcla": bt.por_familia(mez_m),
                "v2 calib.": bt.por_familia(mod_m),
                "mercado": bt.por_familia(mer_m),
            })
            print("  Curva del peso del mercado:", {w: round(v, 4) for w, v in curva.items()})

        metr_b = bt.por_familia(mezcla_b)
        base_por_fam = bt.por_familia(base_b)
        familias = bt.familias_habilitadas(metr_b, base_por_fam)
        punt_v2 = bt.puntuacion(cal_comun)
        punt_leg = bt.puntuacion(legacy_comun)
        punt_base = bt.puntuacion(bt.eventos_base(matches, cal_comun))
        ok, motivos = bt.debe_promoverse(punt_v2, punt_leg, punt_base)
        print(f"\nPuntuación B (log-loss medio por familia): v2 {punt_v2:.4f} · actual {punt_leg:.4f} · base {punt_base:.4f}")
        print("Familias habilitadas para picks:", familias or "ninguna")
        print("Puerta de promoción:", "PASA" if ok else "NO PASA — " + "; ".join(motivos))

        cal_final = bt.ajustar_calibracion(eventos)
        print("Calibración final (A+B):", {k: (round(v.a, 3), round(v.b, 3), v.n) for k, v in cal_final.por_familia.items()})
        h = mejor.hyper
        tag = f"dc-ridge-{ahora:%Y%m%d-%H%M%S}"
        version = ModelVersion(
            tag=tag,
            status=MODEL_STATUS_CANDIDATE,
            hyperparams=h.__dict__.copy(),
            calibration=cal_final.to_dict(),
            market_weight=peso,
            enabled_families=familias,
            backtest_metrics={
                "ventana_a": [args.desde, args.corte],
                "ventana_b": [args.corte, ahora.date().isoformat()],
                "puntuacion_b": {"v2": punt_v2, "actual": punt_leg, "base": punt_base},
                "por_familia_b": {k: v.__dict__ for k, v in metr_b.items()},
                "curva_peso_mercado": {str(k): v for k, v in curva.items()},
                "puerta": {"pasa": ok, "motivos": motivos},
            },
        )
        session.add(version)
        if args.promover and ok:
            promover(session, version)
        session.commit()
        estado = "ACTIVA" if version.status == "active" else "candidata"
        print(f"\nVersión {tag} guardada como {estado}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
