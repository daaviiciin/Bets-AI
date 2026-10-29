# Bets AI

Herramienta para **analizar cuotas de fútbol** (sobre todo **Winamax ES**), **cruzarlas con un modelo Poisson / Dixon–Coles** y **priorizar líneas con valor esperado (EV)** frente a un bookie de referencia. Incluye **autenticación por usuario**, **picks del día**, **combinadas guardadas** y **stats post-partido** vía Flashscore.

> **Nota:** todos los datos salen de **Flashscore** (estadísticas) y **Winamax** (cuotas). Sofascore y The Odds API se retiraron del código en octubre de 2026.

## Qué incluye hoy

1. **Pipeline de datos** (`python -m backend.tools.daily_refresh`, lo lanza la tarea programada), todo desde **Flashscore**: calendario, resultados, clasificación, histórico de la temporada actual, **stats por equipo** (xG, tiros, córners, tarjetas… y **goles, córners y tarjetas por mitad**), **stats por jugador** (tiros a puerta, minutos, nota…) y **bajas/dudas** por partido. Las cuotas de **player props** de Winamax se enlazan con esos mismos jugadores.
2. **Cuotas Winamax** ingeridas desde el HTML (`PRELOADED_STATE`) con **curl_cffi** y el catálogo de partidos por WebSocket (`python -m backend.tools.import_winamax_props`, o el botón «Actualizar cuotas» del dashboard).
3. **Modelo v2**: Dixon–Coles con encogimiento, backtest walk-forward, calibración y versión activa en BD (`python -m backend.tools.backtest_modelo --promover`).
4. **Picks del día**: se generan una vez al día y se congelan; la página «Transparencia» mide su acierto.
5. **Dashboard de cuotas calificadas** (`GET /dashboard/graded-odds`): todas las líneas Winamax del horizonte con **probabilidad modelo**, **EV**, λ del Poisson y **viñetas de resumen en español** (legibles, orientadas al mercado).
6. **Auth JWT** (`POST /auth/login`, `GET /auth/me`) y **combinadas guardadas** (`/me/parlays`) con recomputación de resultado cuando los partidos están finalizados.
7. **Frontend Angular**: login, dashboard, página de partido, «Transparencia» y «Mis apuestas».
8. **Baloncesto** (soporte inicial): modelo propio en `basketball_model.py`.

## Stack técnico

| Capa | Tecnología |
|------|------------|
| Backend | Python 3.12+, FastAPI, Pydantic v2 |
| Base de datos | PostgreSQL 16 (Docker), SQLAlchemy 2, **Alembic** |
| Scraping / HTTP | **curl_cffi**, `websockets` (Socket.IO de Winamax), Pillow (colores de escudos) |
| Frontend | Angular 21, Tailwind CSS v4 |
| Modelo | Poisson / Dixon–Coles (`numpy`, `scipy`) |
| Jobs | Tarea programada de Windows (`refresh_programado.ps1`) |

## Mercados y fuentes

### Winamax (ingesta principal)

Entre otros: **1X2**, **doble oportunidad**, **totales de goles**, **BTTS**, **goles por equipo**, **totales de córners / tarjetas / tiros / tiros a puerta**, **totales de 1ª parte** (goles, córners, tarjetas y goles por equipo local/visitante cuando Winamax los publica), y **player props** (tiros, tiros a puerta, tarjetas, goleador, etc.). Los `betType` concretos están mapeados en `backend/scrapers/winamax.py`.

### Flashscore

Feeds internos tipo `global.flashscore.ninja`: fixtures, página de clasificación (con todos los partidos jugados de la temporada), **stats de partido** (`df_st`, con secciones partido / 1ª parte / 2ª parte) e **incidencias** (`df_su`, goles por mitad). API GraphQL `pq_graphql` para **stats por jugador** (`epmsse`/`epmsd`) y **bajas** (`dmpe2`). Son feeds internos: no se lanzan en bucle (pueden bloquear por IP).

## Frontend (rutas)

| Ruta | Descripción |
|------|-------------|
| `/login` | Inicio de sesión |
| `/dashboard` | Portada: picks del día, partidos de la jornada con su 1X2 y botón «Actualizar cuotas» |
| `/partido/:id` | Página de partido: previa, cuotas calificadas, estadísticas, alineaciones y jugadores |
| `/transparencia` | Acierto de los picks del día del modelo, racha e histórico |
| `/mis-apuestas` | Combinadas guardadas con importe, beneficio, ROI y corrección manual |

Todas salvo `/login` requieren sesión; cualquier otra ruta redirige a `/dashboard`.

Proxy de desarrollo: `frontend/proxy.conf.json` reenvía `/api` al backend (`uvicorn` en `:8000`).

## API REST (resumen)

Documentación interactiva: **`http://127.0.0.1:8000/docs`** (solo con `ENVIRONMENT=development`).
Todo salvo `/` y `/auth/login` exige `Authorization: Bearer <token>`.

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/auth/login` | JWT (body: usuario / contraseña) |
| `GET` | `/auth/me` | Usuario actual (Bearer) |
| `GET` | `/matches` | Partidos próximos (con `with_odds=true`, su último 1X2) |
| `GET` | `/matches/{id}/preview` | Todo lo de la página de partido |
| `GET` | `/dashboard/graded-odds` | Cuotas Winamax calificadas + resumen textual |
| `POST` | `/refresh/odds` | Releer cuotas Winamax (botón del dashboard) |
| `GET` / `POST` | `/featured-picks/today` | Picks de hoy (el `POST` los genera si faltan) |
| `GET` | `/featured-picks/history` | Histórico por día |
| `GET` | `/featured-picks/metrics` | Acierto y racha |
| `POST` | `/featured-picks/refresh-grades` | Corregir picks con los resultados |
| `GET` | `/me/parlays` | Combinadas guardadas |
| `POST` | `/me/parlays` | Crear combinada desde patas snapshot |
| `PATCH` | `/me/parlays/{id}` | Cambiar importe o corregir el resultado a mano |
| `DELETE` | `/me/parlays/{id}` | Borrar |
| `POST` | `/me/parlays/refresh-grades` | Recalcular aciertos/fallos |
| `GET` | `/me/parlays/metrics-summary` | Beneficio, ROI y % de acierto |

## Setup local

### Requisitos

- Python **3.12+**
- Node.js **22+** y Angular CLI acorde al proyecto
- Docker Desktop (**PostgreSQL**)

### Pasos

```powershell
git clone <repo_url>
cd "Bets AI"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# Edita .env: contraseña de PostgreSQL, DATABASE_URL y JWT_SECRET

docker compose up -d

# Obligatorio antes de arrancar la API (esquema SQLAlchemy != BD vacía)
alembic upgrade head
```

### Arrancar en desarrollo (3 terminales)

Como alternativa, en Windows puedes ejecutar `start_bets_ai.bat` desde la raiz
del proyecto. El script usa Python 3.13 (crea el `.venv` si falta), comprueba Docker, levanta PostgreSQL, aplica las
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

- Calendario, resultados y stats: `.\.venv\Scripts\python.exe -m backend.tools.daily_refresh` desde la raíz.
- Cuotas: `.\.venv\Scripts\python.exe -m backend.tools.import_winamax_props --days-ahead 5 --all-leagues`, o el botón «Actualizar cuotas» del dashboard.
- Usuario de la app: `.\.venv\Scripts\python.exe -m backend.tools.set_password <usuario> --crear`.
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
│   ├── services/            # Ingesta, modelo, graded odds, picks, combinadas…
│   ├── scrapers/            # winamax, flashscore
│   ├── tools/               # Scripts CLI (refresh, cuotas, picks, backtest, usuarios)
│   └── tests/
├── frontend/                # Angular (proxy → /api)
├── migrations/              # Alembic (revisiones versionadas)
├── docker-compose.yml
├── alembic.ini
├── requirements.txt
└── .env.example
```

## Roadmap / historia corta

Las fases antiguas del README inicial quedaron **superadas**: ya hay API, scrapers Winamax/Flashscore, modelo, dashboard y usuarios. El trabajo actual (fase 3: modelo estadístico y de predicción) está en [HOJA_DE_RUTA.md](HOJA_DE_RUTA.md) y [PLAN_FASE_3.md](PLAN_FASE_3.md).

## Aviso legal

Proyecto **educativo / portfolio**. No es asesoramiento financiero. El juego puede generar adicción; apuesta solo dinero que puedas permitirte perder.
