# Bets AI

Herramienta para **analizar cuotas de fútbol** (sobre todo **Winamax ES**), **cruzarlas con un modelo Poisson / Dixon–Coles** y **priorizar líneas con valor esperado (EV)** frente a un bookie de referencia. Incluye **autenticación por usuario**, **combinadas guardadas** y **stats post-partido** vía Flashscore.

> **Nota:** la app actual se centra en el flujo **datos oficiales + Winamax + dashboard**; la integración con **The Odds API** sigue disponible pero va **desactivada por defecto** (ver `.env.example`).

## Qué incluye hoy

1. **Pipeline de datos** (`POST /refresh/official` o `python -m backend.tools.daily_refresh`), todo desde **Flashscore** (Sofascore responde `403` desde octubre de 2026 y ya no se usa): calendario, resultados, clasificación, histórico de la temporada actual, **stats por equipo** (xG, tiros, córners, tarjetas… y **goles, córners y tarjetas por mitad**), **stats por jugador** (tiros a puerta, minutos, nota…) y **bajas/dudas** por partido. Las cuotas de **player props** de Winamax se enlazan con esos mismos jugadores.
2. **Cuotas Winamax** ingeridas desde el HTML (`PRELOADED_STATE`) con **curl_cffi** (sin navegador obligatorio para el catálogo principal).
3. **The Odds API** (opcional): capa extra de bookmakers si activas `ODDS_API_REFRESH_ENABLED` y defines `ODDS_API_KEY`.
4. **Backend FastAPI**: partidos, cuotas, análisis por partido, value bets, calibración, jugadores, refresco.
5. **Dashboard de cuotas calificadas** (`GET /dashboard/graded-odds`): todas las líneas Winamax del horizonte con **probabilidad modelo**, **EV**, λ del Poisson y **viñetas de resumen en español** (legibles, orientadas al mercado).
6. **Auth JWT** (`POST /auth/login`, `GET /auth/me`) y **combinadas guardadas** (`/me/parlays`) con recomputación de resultado cuando los partidos están finalizados.
7. **Frontend Angular**: login, dashboard de cuotas, página «Mis apuestas».

## Stack técnico

| Capa | Tecnología |
|------|------------|
| Backend | Python 3.12+, FastAPI, Pydantic v2 |
| Base de datos | PostgreSQL 16 (Docker), SQLAlchemy 2, **Alembic** |
| Scraping / HTTP | `requests`, BeautifulSoup, **curl_cffi**, Playwright (dependencia presente; Winamax usa principalmente curl_cffi) |
| Frontend | Angular 21, Tailwind CSS v4 |
| Modelo | Poisson / Dixon–Coles (`scipy`, `pandas`) |
| Jobs | APScheduler (opcional en despliegue) |
| LLM | Configurable (`google-generativeai` en requirements; uso según implementación de explicaciones) |

## Mercados y fuentes

### Winamax (ingesta principal)

Entre otros: **1X2**, **doble oportunidad**, **totales de goles**, **BTTS**, **goles por equipo**, **totales de córners / tarjetas / tiros / tiros a puerta**, **totales de 1ª parte** (goles, córners, tarjetas y goles por equipo local/visitante cuando Winamax los publica), y **player props** (tiros, tiros a puerta, tarjetas, goleador, etc.). Los `betType` concretos están mapeados en `backend/scrapers/winamax.py`.

### The Odds API (opcional)

Desactivado por defecto para no consumir créditos. Mercados típicos configurados en el proyecto: `h2h`, `double_chance`, `totals`, etc.

### Flashscore

Feeds internos tipo `global.flashscore.ninja`: fixtures, página de clasificación (con todos los partidos jugados de la temporada), **stats de partido** (`df_st`, con secciones partido / 1ª parte / 2ª parte) e **incidencias** (`df_su`, goles por mitad). API GraphQL `pq_graphql` para **stats por jugador** (`epmsse`/`epmsd`) y **bajas** (`dmpe2`). Son feeds internos: no se lanzan en bucle (pueden bloquear por IP).

## Frontend (rutas)

| Ruta | Descripción |
|------|-------------|
| `/` | Login (si ya hay sesión, redirige a `/dashboard`) |
| `/dashboard` | Tabla de cuotas Winamax calificadas, KPIs y guardado de combinadas |
| `/mis-apuestas` | Lista de combinadas guardadas (requiere login) |

Proxy de desarrollo: `frontend/proxy.conf.json` reenvía `/api` al backend (`uvicorn` en `:8000`).

## API REST (resumen)

Documentación interactiva: **`http://127.0.0.1:8000/docs`**.

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/auth/login` | JWT (body: usuario / contraseña) |
| `GET` | `/auth/me` | Usuario actual (Bearer) |
| `GET` | `/me/parlays` | Combinadas guardadas |
| `POST` | `/me/parlays` | Crear combinada desde patas snapshot |
| `DELETE` | `/me/parlays/{id}` | Borrar |
| `POST` | `/me/parlays/refresh-grades` | Recalcular aciertos/fallos |
| `GET` | `/dashboard/graded-odds` | Cuotas Winamax calificadas + resumen textual |
| `GET` | `/matches` | Partidos próximos |
| `GET` | `/matches/{id}` | Detalle |
| `GET` | `/matches/{id}/odds` | Cuotas |
| `GET` | `/matches/{id}/stats` | Stats Flashscore (si existen) |
| `GET` | `/matches/{id}/analysis` | Análisis modelo |
| `GET` | `/matches/{id}/player-props` | Props agrupadas |
| `GET` | `/value-bets` | Value bets detectadas |
| `GET` | `/profiles` … | Perfiles y recomendaciones |
| `POST` | `/refresh` | Refresco Odds API (si está habilitado) |
| `POST` | `/refresh/results` | Actualizar marcadores |
| `POST` | `/refresh/daily` | Orquestación diaria |
| `POST` | `/refresh/official` | **Pipeline oficial** (Flashscore + Winamax según flags) |

## Setup local

### Requisitos

- Python **3.12+**
- Node.js **22+** y Angular CLI acorde al proyecto
- Docker Desktop (**PostgreSQL**)
- Opcional: `ODDS_API_KEY`, API keys LLM según uses esas rutas

### Pasos

```powershell
git clone <repo_url>
cd "Bets AI"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# Edita .env: DATABASE_URL, y si aplica ODDS_API_KEY / flags del pipeline

docker compose up -d

# Obligatorio antes de arrancar la API (esquema SQLAlchemy != BD vacía)
alembic upgrade head
```

### Arrancar en desarrollo (3 terminales)

Como alternativa, en Windows puedes ejecutar `start_bets_ai.bat` desde la raiz
del proyecto. El script usa Python 3.10, comprueba Docker, levanta PostgreSQL, aplica las
migraciones y abre el backend y el frontend en terminales separadas.

```powershell
# 1) PostgreSQL
docker compose up -d

# 2) API
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload

# 3) Frontend
cd frontend
npm install
npm start
```

| Servicio | URL |
|----------|-----|
| Angular | http://localhost:4200 |
| API | http://127.0.0.1:8000 |
| Swagger | http://127.0.0.1:8000/docs |

### Poblar datos

- Tras levantar la API: **`POST /refresh/official`** (desde Swagger o tu cliente HTTP) para sincronizar fixtures y cuotas Winamax según `.env`.
- Stats (sin cuotas): `.\.venv\Scripts\python.exe -m backend.tools.daily_refresh` desde la raíz.
- **Refresh programado (Windows):** la tarea "Bets AI - Refresh Flashscore" lanza
  `refresh_programado.ps1` a las 09:00, 15:00 y 21:00. Si Docker Desktop está cerrado lo abre,
  y deja un log por día en `logs/refresh_AAAA-MM-DD.log`. Quitarla:
  `Unregister-ScheduledTask -TaskName 'Bets AI - Refresh Flashscore'`.

### Cobertura de estadísticas de partido

- Solo **temporada actual**. Los partidos jugados salen de la página de clasificación de cada liga (sin peticiones extra) y sus stats se descargan poco a poco: por refresh, 40 partidos de stats de equipo, 30 de stats por jugador (últimos 60 días) y 40 consultas de bajas, para no arriesgar un bloqueo de IP.
- Cada partido se consulta una vez (las bajas de los próximos, como mucho una vez al día).
- Estado y decisiones: [HOJA_DE_RUTA.md](HOJA_DE_RUTA.md).
- Opta/Stats Perform no ofrece estas estadísticas como un endpoint público para scrapear. Sus productos oficiales incluyen datos históricos y feeds de betting de equipos/jugadores, pero el acceso se gestiona comercialmente: [Opta Data](https://www.statsperform.com/products/opta-data/) y [Bet Trading Data](https://www.statsperform.com/products/bet-trading-data/). Si se contrata, integraríamos el feed documentado con credenciales, no raspando la web pública.

### Tests backend

```powershell
pytest backend/tests -q
```

## Idea de valor (EV)

Para cada línea relevante se puede contrastar la **cuota ofrecida** con una **probabilidad modelo** (Poisson / mercados tipo sharp) y estimar **EV ≈ cuota × P(modelo) − 1**. La confianza mostrada en el dashboard depende del mercado y de si el modelo aplica a esa selección.

## Estructura del proyecto

```
Bets AI/
├── backend/
│   ├── app/                 # FastAPI: main, api/, deps/, schemas/, config
│   ├── models/              # SQLAlchemy
│   ├── services/            # Ingesta, modelo, graded odds, parlays, refresh…
│   ├── scrapers/            # odds_api, winamax, flashscore, sofascore
│   ├── tools/               # Scripts CLI (import stats, etc.)
│   └── tests/
├── frontend/                # Angular (proxy → /api)
├── migrations/              # Alembic (revisiones versionadas)
├── docker-compose.yml
├── alembic.ini
├── requirements.txt
└── .env.example
```

## Roadmap / historia corta

Las fases antiguas del README inicial quedaron **superadas**: ya hay API, scrapers Winamax/Flashscore, modelo, dashboard y usuarios. Lo pendiente o ampliable depende del producto (más deportes, más casas, explicaciones LLM integradas en UI, CI/CD, etc.).

## Aviso legal

Proyecto **educativo / portfolio**. No es asesoramiento financiero. El juego puede generar adicción; apuesta solo dinero que puedas permitirte perder.
