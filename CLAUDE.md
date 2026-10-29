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

Scripts CLI: `python -m backend.tools.<nombre>` desde la raíz. Los de la tarea programada:
`daily_refresh`, `import_winamax_props`, `generar_picks_del_dia`. Manuales: `backtest_modelo`
(backtest + calibración + `--promover` del modelo v2), `diagnostico_modelo`, `set_password`,
`reparar_partidos_en_directo`.

**Python:** el `.venv` usa **3.13** (cambio de PC del 2026-10-29) y `start_bets_ai.bat` arranca la API
con él; si falta el `.venv` lo crea con `py -3.13` e instala `requirements.txt`. `tzdata` es
obligatorio en Windows (`zoneinfo` no trae la base IANA). Usa siempre el `.venv`. No cambies la
versión del `.bat` ni del README sin preguntar.

## Arquitectura

```
backend/
  app/        FastAPI: main.py registra routers de app/api/*, schemas/ (Pydantic v2), deps/ (auth JWT)
  models/     SQLAlchemy 2 (un modelo por archivo, exportados en models/__init__.py)
  services/   Lógica de negocio: ingesta, modelo Poisson/v2, EV, graded odds, combinadas, picks
  scrapers/   winamax (curl_cffi + PRELOADED_STATE + WebSocket), flashscore (feeds df_st/df_su)
  tools/      Scripts CLI
  tests/      pytest, sin conftest ni BD: prueban funciones puras de services/scrapers
frontend/src/app/
  core/       api.service.ts (único cliente HTTP), models.ts (tipos), format.ts (formateo), auth.*
  pages/      Una carpeta por página: <nombre>.ts + <nombre>.html
migrations/   Alembic
```

Flujo de datos: `tools/daily_refresh` → Flashscore (calendario, resultados, clasificación, stats de
equipo/jugador, bajas: `services/daily_refresh.py`) → `tools/import_winamax_props` (cuotas; los props
se enlazan con los jugadores de Flashscore) → `graded_odds_service` calcula P(modelo) y EV →
`GET /dashboard/graded-odds`. Desde la app solo se refrescan cuotas (`POST /refresh/odds`, botón del
dashboard). Sofascore (`403` desde 2026-10) y The Odds API se retiraron del código el 2026-10-28; las
columnas que dejaron en BD (`sofascore_event_id`, `snapshots.source`…) siguen ahí.
Refresh automático: tarea de Windows → `refresh_programado.ps1` (hora fijada en UTC: 09/15/21 h en
verano, 08/14/20 h en invierno; log en `logs/`). Con el portátil en suspensión se pierden tandas:
ver HOJA_DE_RUTA. Al final genera los **picks del día** si faltan; la portada también los genera
al abrirse (`POST /featured-picks/today`). Una vez guardados no cambian (trigger en BD).

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
- No lances scrapers contra Winamax/Flashscore en bucle ni para probar: usa los tests con
  HTML/JSON de ejemplo. Los feeds son internos y pueden bloquear por IP.
- El README se puso al día el 2026-10-28; si no cuadra con el código, el código manda.
- La API solo expone lo que usa el frontend (depuración del 2026-10-28). Antes de añadir un endpoint,
  un servicio o un script, comprueba que algo lo va a llamar.
- Existe soporte inicial de **baloncesto** (`basketball_model.py`, migración `f8a2c1eeb9aa`).

## Skills para este proyecto

| Tarea | Skill |
|---|---|
| Documentación de librerías o repos (`curl_cffi`, FastAPI, SQLAlchemy, Alembic, Angular…) | `find-docs` (context7), **siempre antes que la búsqueda web** |
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

1. **Cerrada (2026-10-16): scraping de estadísticas mucho más completo.** Última tarea: cuotas de
   Winamax por liga vía WebSocket (`tournament:<id>`, 5 días vista). El backfill de stats sigue solo.
   Cada dato nuevo que se añada necesita modelo + migración + parser con test.
2. **Cerrada: mejorar UX/UI** del dashboard (commit `a181389`).
3. **Ahora: mejorar el modelo estadístico** y el **modelo de predicción de apuestas**. Plan por
   bloques (B0–B13) y decisiones del usuario en [PLAN_FASE_3.md](PLAN_FASE_3.md); investigación de
   modelos externos en [INVESTIGACION_MODELOS.md](INVESTIGACION_MODELOS.md).

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
