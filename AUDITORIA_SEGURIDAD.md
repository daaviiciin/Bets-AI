# Auditoría de seguridad — Bets AI (2026-10-17)

Alcance: código actual (backend, frontend, scripts, Docker) e historial git completo (12 commits,
objetos colgantes incluidos) y dependencias (`pip-audit`, `npm audit`).

> **Ojo:** si el repo de GitHub es público, no subas este fichero hasta corregir C-1 a C-3:
> describe cómo explotarlas.

## Resumen

- **Historial limpio:** ninguna clave real en ningún commit; `.env` nunca se ha subido;
  `.env.example` solo tiene marcadores. **No hay que rotar ninguna clave por el historial.**
- **Lo grave está en el código actual:** el secreto JWT es el de serie (cualquiera puede
  hacerse pasar por cualquier usuario, probado contra la API), el usuario `admin` / `1234` y los
  endpoints de refresh sin autenticación.

## Prioridad 1 — corregir ya (propuesto, no aplicado: toca login/auth)

| ID | Sev. | Dónde | Qué pasa | Cómo arreglarlo |
|---|---|---|---|---|
| C-1 | Crítica | `backend/app/config.py:83-86` | `jwt_secret` usa el valor por defecto del código, y es el que está en uso. Con el repo a la vista se firma un token válido para cualquier `sub` (probado: `GET /auth/me` → 200). | Generar uno (`python -c "import secrets;print(secrets.token_urlsafe(48))"`), ponerlo como `JWT_SECRET` en `.env`, añadir `JWT_SECRET=` a `.env.example` y quitar el default (o fallar al arrancar fuera de `development`). Invalida las sesiones: hay que volver a entrar. |
| C-2 | Alta | migración `aa11bb22cc33`, `login.html:18` | Usuario `admin` con contraseña `1234`, y la pista sale en la pantalla de login. | CLI `backend/tools/set_password.py` para cambiarla y quitar la pista de la UI. |
| C-3 | Alta (al desplegar) / Media (local) | `app/api/refresh.py`, `featured_daily_picks.py:72,80` | Todos los `POST /refresh*` y 2 de featured-picks funcionan sin login: cualquiera que llegue a la API (o una web maliciosa vía CSRF a `127.0.0.1:8000`) puede lanzar los scrapers en bucle → bloqueo de IP. | `dependencies=[Depends(get_current_user)]` en el router `/refresh` y en esos dos POST. El botón del dashboard ya envía el token; la tarea programada usa la CLI y no le afecta. |

## Prioridad 2 — pronto

| ID | Sev. | Dónde | Qué pasa | Cómo arreglarlo |
|---|---|---|---|---|
| D-1 | Alta | `requirements.txt` | 47 vulnerabilidades en 7 paquetes: starlette 0.41.3 (DoS multipart/Range, validación de Host), PyJWT 2.10.1 (casi todas no aplican con HS256; sí `crit`), curl-cffi 0.7.4 (redirecciones a IPs internas), requests, lxml, python-dotenv, pytest. | Subir FastAPI (→ starlette ≥ 1.3.1), PyJWT ≥ 2.15, curl-cffi ≥ 0.15, requests ≥ 2.33, lxml ≥ 6.1. Probable salto mayor en FastAPI: probar con los tests. |
| D-2 | Media | `frontend/package-lock.json` | 38 avisos (3 críticos, 21 altos), casi todos en herramientas de build/dev (piscina, tar, vite, esbuild…). En runtime: Angular 21.2.11 (XSS por bypass de saneado; no explotable hoy: no hay `innerHTML` ni SSR). | `ng update @angular/core@21 @angular/cli@21` + `npm update`; luego `npm audit`. Nada de `audit fix --force`. |
| C-4 | Media | `services/auth_password.py:19-24` | Comparación de hash no constante; PBKDF2 con 120k iteraciones (OWASP: 600k). | `hmac.compare_digest` y 600k con migración en el siguiente login. |
| C-5 | Media | `app/api/auth.py:25-37` | Login sin límite de intentos; enumeración de usuarios por tiempo. | Límite por IP/usuario (slowapi, 5/min) y hash ficticio si el usuario no existe. |
| C-9 | Media | `core/auth.service.ts` | JWT en `localStorage` con 30 días de vida (un XSS lo robaría; no se ha encontrado ninguno). | TTL de 1 día; más adelante cookie `HttpOnly; SameSite=Strict`. |
| H-2 / C-12 | Media | `docker-compose.yml` | Postgres publicado en todas las interfaces (accesible desde la wifi). Tu contraseña real no es la de serie, pero el fallback `changeme` sigue en compose y `config.py`. | `"127.0.0.1:${POSTGRES_PORT:-5432}:5432"` y quitar el fallback. |

## Prioridad 3 — cuando se despliegue / menores

- **C-10 (Baja):** CORS amplio para cualquier puerto de localhost; en producción, lista explícita y `allow_credentials=False`.
- **C-11 (Baja):** `/docs`, `/redoc` y `/openapi.json` siempre abiertos; desactivarlos en producción.
- **H-5 (Baja):** `.gitignore` no cubre `.env.prod`/`.env.production` ni todo `logs/`: añadir `.env.*` + `!.env.example` y `logs/`.
- **C-13 (Info):** el boletín de `localStorage` no se valida pata a pata; filtrar por forma y limitar a 30.
- **H-6 (Info):** commits `97f06a4` y `dd1337d` con un correo corporativo como autor; si importa, `.mailmap` (borrarlo exigiría reescribir el historial).
- **H-7 (Info):** `.claude/settings.json` (versionado) contiene rutas con tu usuario de Windows.
- **H-8 (Info):** un stash descartado y blobs huérfanos locales (sin secretos); opcional `git gc --prune=now`.
- **H-9 (Info):** no se pudo comprobar si el repo es público (`gh` no instalado). Mira en GitHub → Settings y activa *Dependabot alerts*, *Secret scanning* y *Push protection*.

## Corregido en esta auditoría (con tests)

- **C-6:** los errores de red de la Odds API llevaban la URL con `?apiKey=…` a los logs y a la respuesta de `POST /refresh`. Ahora se enmascara (`ocultar_api_key()` en `scrapers/odds_api.py`).
- **C-7:** búsqueda de jugadores (`q`) sin longitud máxima y con `%`/`_` sin escapar (`q=%%` devolvía todo). Ahora `max_length=80` y LIKE escapado (`services/busqueda_texto.py`).
- **C-8:** combinadas y apuestas manuales sin límites (JSON enorme, cuota `1e400` → error 500). Ahora máx. 30 patas, cuota 1–1000 y textos acotados.
- Tests nuevos: `backend/tests/test_seguridad_entradas.py` (13). Total backend: **536 en verde**, ruff limpio.

## Revisado y sin problemas

SQL siempre parametrizado (ORM); sin `subprocess`/`eval`/`pickle`/`yaml.load`; JWT con algoritmo
fijado y `exp`; sin IDOR en `/me/parlays`; errores sin trazas; scrapers con timeout, TLS verificado
y hosts fijos; parámetros de rutas acotados; uvicorn y el proxy de Angular solo en `127.0.0.1`;
`refresh_programado.ps1` y `start_bets_ai.bat` sin credenciales; sin XSS en el frontend.

## Aplicado el 2026-10-19

| ID | Estado | Qué se hizo |
|---|---|---|
| C-1 | Aplicado | `JWT_SECRET` nuevo (`token_urlsafe(48)`) escrito en `.env` sin mostrarlo; `JWT_SECRET=` en `.env.example`. Sin valor por defecto en `config.py`: `comprobar_config_api()` hace fallar el arranque si falta, mide < 32 caracteres o es el antiguo. TTL del token: **7 días** (antes 30). Los tokens firmados con el secreto antiguo dan 401 (probado). |
| C-2 | Aplicado (falta la contraseña) | Quitada la pista "admin / 1234" y el autorrelleno del login, y también el enlace "Ir al dashboard (sin sesión)". CLI nueva `python -m backend.tools.set_password <usuario> [--crear]` (getpass dos veces, mín. 10 caracteres). **La contraseña de `admin` sigue siendo la antigua hasta que el usuario ejecute la CLI.** |
| C-3 | Aplicado | Todos los routers salvo `auth` se registran con `dependencies=[Depends(get_current_user)]` en `main.py` (públicos solo `GET /` y `POST /auth/login`). `POST /refresh*` y los dos POST de featured-picks llevan además límite por usuario. La tarea programada usa la CLI (`backend.tools.daily_refresh`, `import_winamax_props`): no le afecta. |
| C-5 | Aplicado | Librería `limits` (la de debajo de slowapi), en memoria, configurable en Settings: login 5/min por IP, `POST /refresh*` 1 cada 3 min por usuario (cupo compartido; featured-picks otro igual), resto 120/min por IP. 429 en español con `Retry-After`. Hash ficticio si el usuario no existe. |
| C-4 | **Pendiente** (no elegido) | Sigue la comparación `==` y PBKDF2 con 120k iteraciones. |
| C-9 | Parcial | TTL a 7 días; el token sigue en `localStorage`. |
| C-10 | Aplicado | CORS con lista explícita `CORS_ALLOWED_ORIGINS` (por defecto `http://localhost:4200,http://127.0.0.1:4200`), `allow_credentials=False`, métodos y cabeceras acotados. |
| C-11 | Aplicado | `/docs`, `/redoc` y `/openapi.json` solo con `ENVIRONMENT=development`. |
| Cabeceras | Aplicado | Middleware: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`, CSP `default-src 'none'; frame-ancestors 'none'` en la API (en `/docs` solo `frame-ancestors`). No afecta al frontend (fotos de static.flashscore.com y escudos los carga Angular, servido aparte). Límite de cuerpo 1 MiB (413). |
| H-2 / C-12 | Aplicado | Postgres publicado solo en `127.0.0.1`; sin fallback `changeme` (compose exige `POSTGRES_PASSWORD`, `config.py` da error claro). Contenedor recreado: healthy, `alembic current` = head, datos intactos (volumen con nombre `betsai_postgres_data`). |
| H-5 | Aplicado | `.gitignore`: `.env.*` + `!.env.example`, `logs/`. |
| D-1 | Aplicado | fastapi 0.115.5 → 0.149.0 (starlette 0.41.3 → 1.7.0), PyJWT 2.10.1 → 2.15.1, curl_cffi 0.7.4 → 0.16.3, requests 2.32.3 → 2.34.2, lxml 5.3.0 → 6.1.3, python-dotenv 1.0.1 → 1.2.4, pydantic 2.10.3 → 2.14.1, pydantic-settings 2.7.0 → 2.15.0, uvicorn 0.32.1 → 0.54.0, pytest 8.3.4 → 9.1.1, pytest-cov 6.0.0 → 7.1.0; nuevos `limits` 5.8.0, `httpx` 0.28.1 (TestClient), `pip-audit` 2.10.1. setuptools del .venv 65.5 → ≥ 83. `pip-audit`: **0 vulnerabilidades** (requirements y .venv). |
| D-2 | Aplicado | Angular 21.2.11 → 21.2.25, CLI/build 21.2.9 → 21.2.27, `npm update`. `npm audit`: **0** (antes 38). |
| H-6…H-9, C-13 | Pendiente | Sin cambios (informativos). |

Tests nuevos: `backend/tests/test_seguridad_api.py` (46). Backend 601 en verde, ruff limpio; frontend build OK y 29 tests en verde.

**Qué tiene que hacer el usuario:** `python -m backend.tools.set_password admin`; reiniciar la API (la que estaba abierta en :8000 sigue con el código viejo); volver a iniciar sesión (los tokens viejos ya no valen).
