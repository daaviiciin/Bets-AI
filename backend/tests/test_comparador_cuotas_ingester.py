"""Tests del paso «comparador de cuotas» del refresh (``services/comparador_cuotas_ingester.py``).

Sin BD ni red: la sesión y la descarga son dobles. Lo que se prueba es que el paso no corta nunca
el pipeline, que respeta la configuración y que una racha de fallos corta la tanda.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.services import comparador_cuotas_ingester as cci

LOG = logging.getLogger("test_comparador")
AHORA = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


class _Resultado:
    def __init__(self, partidos: list) -> None:
        self._partidos = partidos

    def all(self) -> list:
        return self._partidos


class _SesionFalsa:
    """Devuelve los partidos dados en ``scalars`` y cuenta commits y rollbacks."""

    def __init__(self, partidos: list | None = None) -> None:
        self.partidos = partidos or []
        self.commits = 0
        self.rollbacks = 0

    def scalars(self, _stmt) -> _Resultado:
        return _Resultado(self.partidos)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _partidos(n: int) -> list:
    return [SimpleNamespace(id=i, external_id=f"flashscore:ev{i}") for i in range(1, n + 1)]


def _resultado(**kw) -> cci.ComparadorIngestResult:
    return cci.ComparadorIngestResult(started_at=AHORA, finished_at=AHORA, **kw)


class TestCorteTrasFallosSeguidos:
    def _ingest(self, fetch, n: int = 10, **kw) -> tuple[cci.ComparadorIngestResult, list[str]]:
        pedidos: list[str] = []

        def fetch_contado(fs_id: str) -> dict:
            pedidos.append(fs_id)
            return fetch(fs_id)

        sesion = _SesionFalsa(_partidos(n))
        ingester = cci.ComparadorCuotasIngester(
            sesion, log=LOG, fetch=fetch_contado, sleep_seconds=0,
        )
        res = ingester.ingest(match_ids=[p.id for p in sesion.partidos], **kw)
        return res, pedidos

    def test_bloqueo_corta_la_tanda_a_los_3_fallos(self) -> None:
        def bloqueado(_fs_id: str) -> dict:
            raise RuntimeError("HTTP 403")

        res, pedidos = self._ingest(bloqueado)
        assert pedidos == ["ev1", "ev2", "ev3"]
        assert res.cortado
        assert res.fetch_errors == 3
        assert res.matches_fetched == 0

    def test_un_acierto_reinicia_la_cuenta(self) -> None:
        def alterno(fs_id: str) -> dict:
            if int(fs_id.removeprefix("ev")) % 3:  # fallan 1, 2, 4, 5…; aciertan 3, 6, 9
                raise TimeoutError("timeout")
            return {}  # respuesta vacía: se parsea sin cuotas y no toca la BD

        res, pedidos = self._ingest(alterno, n=9)
        assert len(pedidos) == 9
        assert not res.cortado
        assert res.matches_fetched == 3
        assert res.fetch_errors == 6

    def test_el_tope_es_configurable(self) -> None:
        def bloqueado(_fs_id: str) -> dict:
            raise RuntimeError("HTTP 403")

        res, pedidos = self._ingest(bloqueado, max_fallos_seguidos=5)
        assert len(pedidos) == 5
        assert res.cortado


class TestErroresDelPaso:
    def test_sin_fallos_no_hay_errores(self) -> None:
        assert cci.errores_del_paso(_resultado(matches_fetched=12)) == []

    def test_los_fallos_se_resumen_en_una_linea(self) -> None:
        res = _resultado(
            matches_fetched=5, fetch_errors=2,
            errors=["match=1 fs=a: HTTP 403", "match=2 fs=b: HTTP 403"],
        )
        assert cci.errores_del_paso(res) == [
            "comparador: 2 de 7 partidos fallaron; primero: match=1 fs=a: HTTP 403",
        ]

    def test_dice_si_se_corto(self) -> None:
        res = _resultado(fetch_errors=3, cortado=True, errors=["match=1 fs=a: HTTP 403"])
        (linea,) = cci.errores_del_paso(res)
        assert "cortado tras 3 seguidos" in linea


class _IngesterFalso:
    """Apunta con qué se llamó a ``ingest`` y devuelve un resultado fijo (o lanza)."""

    llamadas: list[dict] = []

    def __init__(self, session, *, log) -> None:
        self.session = session

    def ingest(self, **kw) -> cci.ComparadorIngestResult:
        _IngesterFalso.llamadas.append(kw)
        return _resultado(matches_eligible=4, matches_fetched=4)


class _IngesterQueRevienta(_IngesterFalso):
    def ingest(self, **kw) -> cci.ComparadorIngestResult:
        raise ConnectionError("Flashscore no responde")


class TestEjecutarPasoComparador:
    @pytest.fixture(autouse=True)
    def _limpia(self) -> None:
        _IngesterFalso.llamadas = []

    def test_apagado_no_lee_nada(self) -> None:
        paso = cci.ejecutar_paso_comparador(
            _SesionFalsa(), log=LOG, settings=_settings(comparador_cuotas_enabled=False),
            ingester_factory=_IngesterFalso,
        )
        assert paso.omitido
        assert paso.errores == []
        assert _IngesterFalso.llamadas == []

    def test_usa_los_dias_y_el_tope_de_la_configuracion(self) -> None:
        paso = cci.ejecutar_paso_comparador(
            _SesionFalsa(), log=LOG,
            settings=_settings(comparador_cuotas_days_ahead=2, comparador_cuotas_max_partidos=15),
            ingester_factory=_IngesterFalso,
        )
        assert _IngesterFalso.llamadas == [{"days_ahead": 2, "max_matches": 15}]
        assert paso.result is not None
        assert paso.errores == []

    def test_los_argumentos_mandan_sobre_la_configuracion(self) -> None:
        cci.ejecutar_paso_comparador(
            _SesionFalsa(), log=LOG, settings=_settings(), days_ahead=1, max_matches=5,
            ingester_factory=_IngesterFalso,
        )
        assert _IngesterFalso.llamadas == [{"days_ahead": 1, "max_matches": 5}]

    def test_por_defecto_5_dias_y_40_partidos(self) -> None:
        cci.ejecutar_paso_comparador(
            _SesionFalsa(), log=LOG, settings=_settings(), ingester_factory=_IngesterFalso,
        )
        assert _IngesterFalso.llamadas == [{"days_ahead": 5, "max_matches": 40}]

    def test_un_fallo_no_lanza_y_vuelve_como_error(self) -> None:
        sesion = _SesionFalsa()
        paso = cci.ejecutar_paso_comparador(
            sesion, log=LOG, settings=_settings(), ingester_factory=_IngesterQueRevienta,
        )
        assert paso.result is None
        assert paso.errores == ["comparador: Flashscore no responde"]
        assert sesion.rollbacks == 1
