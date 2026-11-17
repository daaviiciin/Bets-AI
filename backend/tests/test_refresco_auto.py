"""Tests del refresh automático al abrir la app (``services/refresco_auto.py``), sin BD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import RefreshRun
from backend.services import refresco_auto as ra

AHORA = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)


class _SesionFalsa:
    """Lo justo de ``Session``: ``add``/``commit`` (pone id) y ``get`` de la fila del run."""

    def __init__(self, run: RefreshRun | None = None) -> None:
        self.anadidos: list[RefreshRun] = []
        self.run = run

    def add(self, obj: RefreshRun) -> None:
        self.anadidos.append(obj)

    def commit(self) -> None:
        for obj in self.anadidos:
            obj.id = obj.id or 7

    def rollback(self) -> None:
        pass

    def get(self, _modelo, _id):
        return self.run

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


@pytest.fixture
def politica(monkeypatch):
    """Historial vacío y política fija (la decide ``necesita_refresh``, que se prueba aparte)."""
    respuesta = {"toca": True}
    monkeypatch.setattr(ra, "historial_de_refresh", lambda _s: ra.Historial())
    monkeypatch.setattr(ra, "necesita_refresh", lambda _h, _ahora: respuesta["toca"])
    return respuesta


class TestAsegurar:
    def test_si_no_hace_falta_no_lanza_nada(self, politica) -> None:
        politica["toca"] = False
        lanzados: list[int] = []
        sesion = _SesionFalsa()
        estado = ra.RefrescoAuto(lanzar=lanzados.append).asegurar(sesion, AHORA)
        assert not estado.en_curso
        assert not estado.lanzado
        assert lanzados == []
        assert sesion.anadidos == []

    def test_si_hace_falta_apunta_la_ejecucion_y_la_lanza(self, politica) -> None:
        lanzados: list[int] = []
        sesion = _SesionFalsa()
        estado = ra.RefrescoAuto(lanzar=lanzados.append).asegurar(sesion, AHORA)
        assert estado.en_curso
        assert estado.lanzado
        assert estado.historial.ultimo_inicio == AHORA
        assert lanzados == [7]
        assert sesion.anadidos[0].origen == "app"

    def test_no_lanza_dos_a_la_vez(self, politica) -> None:
        lanzados: list[int] = []
        refresco = ra.RefrescoAuto(lanzar=lanzados.append)
        refresco.asegurar(_SesionFalsa(), AHORA)
        segundo = refresco.asegurar(_SesionFalsa(), AHORA)
        assert segundo.en_curso
        assert not segundo.lanzado
        assert len(lanzados) == 1

    def test_si_falla_al_lanzar_no_se_queda_en_curso(self, politica) -> None:
        def revienta(_id: int) -> None:
            raise RuntimeError("sin hilos")

        refresco = ra.RefrescoAuto(lanzar=revienta)
        with pytest.raises(RuntimeError):
            refresco.asegurar(_SesionFalsa(), AHORA)
        assert not refresco.en_curso


class TestEjecutar:
    @pytest.fixture(autouse=True)
    def _logs_en_tmp(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(ra, "PROJECT_ROOT", tmp_path)

    def _ejecutar(self, monkeypatch, pipeline) -> tuple[RefreshRun, ra.RefrescoAuto]:
        monkeypatch.setattr(ra, "ejecutar_pipeline", pipeline)
        run = RefreshRun(id=7, origen="app", started_at=AHORA)
        refresco = ra.RefrescoAuto(session_factory=lambda: _SesionFalsa(run))
        refresco._en_curso = True
        refresco._ejecutar(7)
        return run, refresco

    def test_sin_errores_queda_ok_y_libre(self, monkeypatch) -> None:
        run, refresco = self._ejecutar(monkeypatch, lambda _s, log: [])
        assert run.finished_at is not None
        assert run.ok is True
        assert run.errores is None
        assert not refresco.en_curso

    def test_con_errores_los_guarda(self, monkeypatch) -> None:
        run, _ = self._ejecutar(monkeypatch, lambda _s, log: ["winamax: timeout"])
        assert run.ok is False
        assert run.errores == ["winamax: timeout"]

    def test_un_fallo_inesperado_cierra_la_ejecucion(self, monkeypatch) -> None:
        def revienta(_s, log):
            raise RuntimeError("BD caída")

        run, refresco = self._ejecutar(monkeypatch, revienta)
        assert run.ok is False
        assert run.errores == ["inesperado: BD caída"]
        assert not refresco.en_curso

    def test_escribe_el_log_del_dia(self, monkeypatch, tmp_path) -> None:
        self._ejecutar(monkeypatch, lambda _s, log: [])
        logs = list((tmp_path / "logs").glob("refresh_*.log"))
        assert len(logs) == 1
        assert "Fin del refresh automático" in logs[0].read_text(encoding="utf-8")


class TestNecesitaRefresh:
    H = timedelta(hours=1)

    def _toca(self, **kw) -> bool:
        return ra.necesita_refresh(ra.Historial(**kw), AHORA)

    def test_nunca_hecho_toca(self) -> None:
        assert self._toca()

    def test_terminado_hace_2_h_no_toca(self) -> None:
        assert not self._toca(ultimo_inicio=AHORA - 2 * self.H, ultimo_fin=AHORA - 2 * self.H)

    def test_terminado_hace_7_h_toca(self) -> None:
        assert self._toca(ultimo_inicio=AHORA - 7 * self.H, ultimo_fin=AHORA - 7 * self.H)

    def test_con_errores_tambien_cuenta_como_hecho(self) -> None:
        hecho = {"ultimo_inicio": AHORA - 2 * self.H, "ultimo_fin": AHORA - 2 * self.H}
        assert not self._toca(**hecho, ultimo_ok=False)

    def test_intento_reciente_sin_terminar_espera(self) -> None:
        assert not self._toca(ultimo_inicio=AHORA - timedelta(minutes=10))

    def test_cortado_hace_mas_de_30_min_se_reintenta(self) -> None:
        # El último terminado es de ayer; el intento de hace 1 h se cortó (sin fin).
        assert self._toca(ultimo_inicio=AHORA - self.H, ultimo_fin=AHORA - 20 * self.H)

    def test_calendario_vacio_toca_aunque_el_ultimo_sea_reciente(self) -> None:
        reciente = {"ultimo_inicio": AHORA - 2 * self.H, "ultimo_fin": AHORA - 2 * self.H}
        assert self._toca(**reciente, calendario_vacio=True)

    def test_calendario_vacio_respeta_la_espera_entre_intentos(self) -> None:
        inicio = AHORA - timedelta(minutes=10)
        assert not self._toca(ultimo_inicio=inicio, ultimo_fin=inicio, calendario_vacio=True)
