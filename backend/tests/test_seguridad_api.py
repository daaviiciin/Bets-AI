"""Tests de seguridad de la API: login obligatorio, JWT, límites, cabeceras, CORS y docs.

No usan PostgreSQL: ``get_db`` se sustituye por una sesión falsa y la app se crea con
``crear_app`` para cada test (limitador nuevo, sin estado compartido).
"""

from __future__ import annotations

import secrets
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, comprobar_config_api, get_settings
from backend.app.database import get_db
from backend.app.deps.rate_limit import LimitadorApi
from backend.services.auth_password import (
    hash_password,
    verificar_password_ficticia,
)
from backend.tools.set_password import validar_password_nueva

SECRETO = secrets.token_urlsafe(48)
SECRETO_ANTIGUO = "betsai_dev_jwt_change_me"
USUARIO = SimpleNamespace(id=1, username="admin", password_hash=hash_password("correcta-123"))


class SesionFalsa:
    """Lo mínimo que usan ``get_current_user`` y ``/auth/login``."""

    def __init__(self, usuario: object | None) -> None:
        self.usuario = usuario

    def scalar(self, _stmt: object) -> object | None:
        return self.usuario

    def close(self) -> None:
        pass


def _token(secreto: str = SECRETO, sub: str = "1", exp_delta: int = 3600) -> str:
    ahora = int(time.time())
    return jwt.encode(
        {"sub": sub, "username": "admin", "iat": ahora, "exp": ahora + exp_delta},
        secreto,
        algorithm="HS256",
    )


@pytest.fixture
def crear_cliente(monkeypatch: pytest.MonkeyPatch):
    """Fábrica: ``crear_cliente(usuario=..., **env)`` → TestClient con la config pedida."""

    def _crear(usuario: object | None = USUARIO, **env: str) -> TestClient:
        valores = {
            "JWT_SECRET": SECRETO,
            "POSTGRES_PASSWORD": "solo-para-tests",
            "ENVIRONMENT": "development",
            "RATE_LIMIT_ENABLED": "true",
            "RATE_LIMIT_LOGIN": "5/minute",
            "RATE_LIMIT_REFRESH": "1 per 3 minutes",
            "RATE_LIMIT_GENERAL": "120/minute",
        }
        valores.update(env)
        for k, v in valores.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        from backend.app.main import crear_app

        app = crear_app(get_settings())
        app.dependency_overrides[get_db] = lambda: SesionFalsa(usuario)
        return TestClient(app)

    yield _crear
    get_settings.cache_clear()


def _auth(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or _token()}"}


class TestArranqueConfig:
    def test_sin_jwt_secret_la_api_no_arranca(self) -> None:
        st = Settings(_env_file=None, jwt_secret="", postgres_password="x")
        with pytest.raises(RuntimeError, match="Falta JWT_SECRET"):
            comprobar_config_api(st)

    def test_jwt_secret_corto_no_vale(self) -> None:
        st = Settings(_env_file=None, jwt_secret="corto", postgres_password="x")
        with pytest.raises(RuntimeError, match="demasiado corto"):
            comprobar_config_api(st)

    def test_el_secreto_antiguo_del_repo_no_vale(self) -> None:
        st = Settings(_env_file=None, jwt_secret=SECRETO_ANTIGUO, postgres_password="x")
        with pytest.raises(RuntimeError):
            comprobar_config_api(st)

    def test_sin_password_de_postgres_error_claro(self) -> None:
        st = Settings(_env_file=None, jwt_secret=SECRETO, postgres_password="", database_url=None)
        with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
            comprobar_config_api(st)

    def test_secreto_largo_y_password_arranca(self) -> None:
        comprobar_config_api(Settings(_env_file=None, jwt_secret=SECRETO, postgres_password="x"))

    def test_ttl_por_defecto_7_dias(self) -> None:
        assert Settings(_env_file=None).jwt_access_ttl_days == 7

    def test_cors_lista_explicita_con_angular(self) -> None:
        assert "http://localhost:4200" in Settings(_env_file=None).cors_origins


class TestLoginObligatorio:
    @pytest.mark.parametrize(
        "ruta",
        ["/matches", "/dashboard/graded-odds", "/players?q=ab", "/value-bets",
         "/featured-picks/today", "/me/parlays", "/auth/me"],
    )
    def test_sin_token_401(self, crear_cliente, ruta: str) -> None:
        r = crear_cliente().get(ruta)
        assert r.status_code == 401

    def test_router_profiles_retirado(self, crear_cliente) -> None:
        # Fase 3: el modelo no sugiere importes (Kelly), así que /profiles ya no existe.
        assert crear_cliente().get("/profiles").status_code == 404

    @pytest.mark.parametrize("ruta", ["/refresh/odds", "/refresh/official", "/refresh",
                                      "/featured-picks/refresh-grades"])
    def test_post_de_refresh_sin_token_401(self, crear_cliente, ruta: str) -> None:
        assert crear_cliente().post(ruta).status_code == 401

    @pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
    def test_token_firmado_con_secreto_antiguo_401(self, crear_cliente) -> None:
        r = crear_cliente().get("/auth/me", headers=_auth(_token(SECRETO_ANTIGUO)))
        assert r.status_code == 401

    def test_token_caducado_401(self, crear_cliente) -> None:
        r = crear_cliente().get("/auth/me", headers=_auth(_token(exp_delta=-10)))
        assert r.status_code == 401

    def test_token_valido_200(self, crear_cliente) -> None:
        r = crear_cliente().get("/auth/me", headers=_auth())
        assert r.status_code == 200
        assert r.json()["username"] == "admin"

    def test_token_de_usuario_inexistente_401(self, crear_cliente) -> None:
        r = crear_cliente(usuario=None).get("/auth/me", headers=_auth())
        assert r.status_code == 401

    def test_health_es_publico(self, crear_cliente) -> None:
        assert crear_cliente().get("/").status_code == 200


class TestLimiteLogin:
    def test_sexto_intento_en_un_minuto_429(self, crear_cliente) -> None:
        cliente = crear_cliente(usuario=None)
        cuerpo = {"username": "nadie", "password": "mala"}
        codigos = [cliente.post("/auth/login", json=cuerpo).status_code for _ in range(6)]
        assert codigos == [401] * 5 + [429]

    def test_429_en_espanol_con_retry_after(self, crear_cliente) -> None:
        cliente = crear_cliente(usuario=None, RATE_LIMIT_LOGIN="1/minute")
        cuerpo = {"username": "nadie", "password": "mala"}
        cliente.post("/auth/login", json=cuerpo)
        r = cliente.post("/auth/login", json=cuerpo)
        assert r.status_code == 429
        assert "Demasiadas peticiones" in r.json()["detail"]
        assert int(r.headers["Retry-After"]) >= 1

    def test_login_correcto_da_token(self, crear_cliente) -> None:
        r = crear_cliente().post(
            "/auth/login", json={"username": "admin", "password": "correcta-123"}
        )
        assert r.status_code == 200
        assert r.json()["access_token"]

    def test_limite_desactivable(self, crear_cliente) -> None:
        cliente = crear_cliente(usuario=None, RATE_LIMIT_ENABLED="false")
        cuerpo = {"username": "nadie", "password": "mala"}
        codigos = {cliente.post("/auth/login", json=cuerpo).status_code for _ in range(8)}
        assert codigos == {401}


class TestHashFicticio:
    def test_usuario_inexistente_siempre_falso(self) -> None:
        assert verificar_password_ficticia("lo-que-sea") is False

    def test_tarda_lo_mismo_que_un_hash_real(self) -> None:
        real = hash_password("x")
        from backend.services.auth_password import verify_password

        t0 = time.perf_counter()
        verify_password("y", real)
        t_real = time.perf_counter() - t0
        t0 = time.perf_counter()
        verificar_password_ficticia("y")
        t_ficticio = time.perf_counter() - t0
        assert t_ficticio > t_real * 0.3


class TestLimiteRefresh:
    def test_segundo_post_de_featured_picks_seguido_429(
        self, crear_cliente, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import featured_daily_picks as fp

        monkeypatch.setattr(fp, "refresh_all_grading", lambda _db: 0)
        cliente = crear_cliente()
        primero = cliente.post("/featured-picks/refresh-grades", headers=_auth())
        segundo = cliente.post("/featured-picks/refresh-grades", headers=_auth())
        assert primero.status_code == 200
        assert segundo.status_code == 429

    def test_segundo_refresh_seguido_429(self, crear_cliente) -> None:
        # Parámetro fuera de rango: FastAPI responde 422 sin entrar en el endpoint (no se
        # lanza ningún scraper), pero el cupo ya se ha gastado en la dependencia.
        cliente = crear_cliente()
        primero = cliente.post("/refresh/odds?days_ahead=99", headers=_auth())
        segundo = cliente.post("/refresh/odds?days_ahead=99", headers=_auth())
        assert primero.status_code == 422
        assert segundo.status_code == 429

    def test_limitador_por_usuario_independiente(self) -> None:
        lim = LimitadorApi()
        assert lim.consumir("1 per 3 minutes", "refresh", "usuario:1") == 0
        assert lim.consumir("1 per 3 minutes", "refresh", "usuario:2") == 0
        espera = lim.consumir("1 per 3 minutes", "refresh", "usuario:1")
        assert 1 <= espera <= 180


class TestLimiteGeneral:
    def test_pasado_el_limite_por_ip_429(self, crear_cliente) -> None:
        cliente = crear_cliente(RATE_LIMIT_GENERAL="3/minute")
        codigos = [cliente.get("/").status_code for _ in range(4)]
        assert codigos == [200, 200, 200, 429]


class TestCabecerasYCors:
    def test_cabeceras_de_seguridad(self, crear_cliente) -> None:
        r = crear_cliente().get("/")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]

    def test_cabeceras_tambien_en_401(self, crear_cliente) -> None:
        r = crear_cliente().get("/matches")
        assert r.headers["X-Content-Type-Options"] == "nosniff"

    def test_cors_permite_angular(self, crear_cliente) -> None:
        r = crear_cliente().get("/", headers={"Origin": "http://localhost:4200"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:4200"

    def test_cors_rechaza_otro_origen(self, crear_cliente) -> None:
        r = crear_cliente().get("/", headers={"Origin": "http://localhost:9999"})
        assert "access-control-allow-origin" not in r.headers

    def test_peticion_demasiado_grande_413(self, crear_cliente) -> None:
        cliente = crear_cliente(MAX_REQUEST_BODY_BYTES="2048")
        r = cliente.post("/auth/login", content=b"x" * 4096,
                         headers={"Content-Type": "application/json"})
        assert r.status_code == 413


class TestDocsSegunEntorno:
    def test_docs_en_desarrollo(self, crear_cliente) -> None:
        cliente = crear_cliente(ENVIRONMENT="development")
        assert cliente.get("/docs").status_code == 200
        assert cliente.get("/openapi.json").status_code == 200

    def test_sin_docs_en_produccion(self, crear_cliente) -> None:
        cliente = crear_cliente(ENVIRONMENT="production")
        assert cliente.get("/docs").status_code == 404
        assert cliente.get("/redoc").status_code == 404
        assert cliente.get("/openapi.json").status_code == 404


class TestSetPassword:
    def test_password_valida(self) -> None:
        assert validar_password_nueva("caballo-bateria-9", "caballo-bateria-9") is None

    def test_no_coinciden(self) -> None:
        assert "coinciden" in validar_password_nueva("caballo-bateria-9", "otra-cosa-123")

    def test_demasiado_corta(self) -> None:
        assert "al menos" in validar_password_nueva("1234", "1234")

    def test_repetitiva(self) -> None:
        assert "repetitiva" in validar_password_nueva("aaaaaaaaaaaa", "aaaaaaaaaaaa")
