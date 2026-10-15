# Bets AI

Herramienta para analizar cuotas de fútbol (sobre todo **Winamax ES**), compararlas con un modelo
**Poisson / Dixon–Coles** y priorizar líneas con **valor esperado (EV ≈ cuota × P(modelo) − 1)**.
Proyecto educativo / portfolio. Todo el dominio, la UI y los comentarios están en **español**.

## Comandos

```powershell
docker compose up -d                                   # PostgreSQL 16 (contenedor betsai_postgres)
.\.venv\Scripts\python.exe -m alembic upgrade head      # obligatorio antes de arrancar la API
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload   # API en :8000, docs en /docs
.\.venv\Scripts\python.exe -m pytest backend/tests -q   # tests backend
.\.venv\Scripts\python.exe -m ruff check backend        # lint
cd frontend; npm start                                  # Angular en :4200 (proxy /api → :8000)
cd frontend; npm test                                   # Vitest
```

Scripts CLI: `python -m backend.tools.<nombre>` desde la raíz (`daily_refresh`, `import_flashscore_stats`,
`calibrate_poisson`, `backtest_poisson`…).

**Python:** el `.venv` usa 3.10, igual que `start_bets_ai.bat` (`py -3.10`); el README dice 3.12+.
Usa siempre el `.venv`. No cambies la versión del `.bat` ni del README sin preguntar.

## Arquitectura

```
backend/
  app/        FastAPI: main.py registra routers de app/api/*, schemas/ (Pydantic v2), deps/ (auth JWT)
  models/     SQLAlchemy 2 (un modelo por archivo, exportados en models/__init__.py)
  services/   Lógica de negocio: ingesta, modelo Poisson, EV, graded odds, combinadas, refresh
  scrapers/   winamax (curl_cffi + PRELOADED_STATE), flashscore (feeds df_st/df_su), sofascore, odds_api
  tools/      Scripts CLI
  tests/      pytest, sin conftest ni BD: prueban funciones puras de services/scrapers
frontend/src/app/
  core/       api.service.ts (único cliente HTTP), models.ts (tipos), format.ts (formateo), auth.*
  pages/      Una carpeta por página: <nombre>.ts + <nombre>.html
migrations/   Alembic
```

Flujo de datos: `POST /refresh/official` → Flashscore (calendario) → Sofascore (evento) → Winamax
(cuotas) → `graded_odds_service` calcula P(modelo) y EV → `GET /dashboard/graded-odds` → dashboard.

## Convenciones

**Backend**
- `from __future__ import annotations` y docstring de módulo en español en cada archivo.
- Dinero, cuotas y probabilidades con `Decimal`, no `float` (ver `value_calculator.py`).
- Los routers solo validan y delegan: la lógica va en `services/`.
- Configuración solo a través de `backend/app/config.py` (`get_settings()`), nunca `os.environ` directo.
- **Si cambias un modelo SQLAlchemy, crea una migración Alembic** (`alembic revision --autogenerate -m "..."`)
  y revísala antes de aplicarla.
- Tests en clases `TestXxx` con nombres descriptivos en español (`test_cuota_2_es_50_pct`).

**Frontend (Angular 21 + Tailwind v4)**
- Componentes standalone, `inject()` en vez de constructor, estado con `signal` / `computed`.
- Control flow nuevo (`@if`, `@for ... track`), nunca `*ngIf` / `*ngFor`.
- Rutas con `loadComponent` en `app.routes.ts`, con `title: '<Página> · Bets AI'`.
- Formateo de cuotas, % y fechas solo con los helpers de `core/format.ts`.
- Prettier: comillas simples, 100 columnas.
- Tema oscuro. Los colores de marca están en `src/styles.scss` (`@theme`: `brand-bg-deep`, `brand-bg-panel`,
  `brand-cyan`, `brand-blue`, `brand-muted`). En código nuevo usa estos tokens; muchas páginas antiguas
  usan `slate-*`/`emerald-*` directamente y conviven ambos estilos.

## Cuidado con

- **No leas ni muestres `.env`** (contiene claves). Para ver qué variables existen usa `.env.example`.
- **No actives `ODDS_API_REFRESH_ENABLED`** ni llames a `POST /refresh` con la Odds API activa: gasta créditos de pago.
- No lances scrapers contra Winamax/Flashscore/Sofascore en bucle ni para probar: usa los tests con
  HTML/JSON de ejemplo. Los feeds son internos y pueden bloquear por IP.
- Hay páginas en `pages/` sin ruta en `app.routes.ts` (`analisis`, `calibration`, `match-detail`,
  `matches`, `recommendations`, `value-bets`). Pregunta antes de borrarlas o enrutarlas.
- El README está parcialmente desactualizado (solo describe 3 páginas y no menciona baloncesto).
  El código manda.
- Existe soporte inicial de **baloncesto** (`basketball_model.py`, migración `f8a2c1eeb9aa`).

## Skills para este proyecto

| Tarea | Skill |
|---|---|
| Diseñar o rediseñar una pantalla del dashboard | `impeccable` (`/impeccable shape`, `critique`, `polish`) |
| Repaso final de detalles antes de dar una UI por terminada | `make-interfaces-feel-better` (modo rápido) |
| Decidir si algo debe animarse y cómo | `emil-design-eng`; para implementarlo, `animate` |
| Tablas o gráficos de cuotas, EV, calibración, KPIs | `dataviz` |
| Probar componentes con datos extremos (nombres largos, 0 partidos, cuotas raras) | `break-ui` |
| Revisar cambios antes de commit | `/code-review` |

**No usar en este proyecto:** `design-taste-frontend` y sus variantes (son para webs públicas, esto es una
app), `ui-styling` (es para shadcn/React), `gpt-taste`, `industrial-brutalist-ui`, `brandkit`,
`imagegen-*`, `image-to-code`, `slides`, `animate-expo`, `write-swift`, `mobile-native`.

## Hoja de ruta (en este orden)

1. **Ahora: scraping de estadísticas mucho más completo.** Objetivo: extraer el máximo de datos
   posibles (equipo, jugador, por mitad, histórico) para poder analizar más. Cada dato nuevo necesita
   modelo + migración + parser con test.
2. **Después: mejorar UX/UI** del dashboard, cuando la fase 1 esté cerrada.
3. **Por último: mejorar el modelo estadístico** y empezar el **modelo de predicción de apuestas**.

**El detalle, el diagnóstico, el estado de cada bloque y las decisiones pendientes están en
[HOJA_DE_RUTA.md](HOJA_DE_RUTA.md).** Léelo al empezar trabajo de cualquier fase y actualiza las
casillas de estado al terminar cada bloque.

No adelantes trabajo de fases posteriores (rediseños, cambios en el modelo Poisson) mientras se
trabaja en una anterior, salvo que se pida. Si un cambio de scraping facilita la fase 3, menciónalo.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
