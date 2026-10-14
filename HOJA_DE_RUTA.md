# Hoja de ruta — Bets AI

Última actualización: 2026-10-14

| Fase | Objetivo | Estado |
|---|---|---|
| **1** | Scraping de estadísticas mucho más completo | **En curso**: diagnóstico hecho, pendiente de empezar |
| 2 | Mejorar UX/UI del dashboard | Pendiente |
| 3 | Mejorar el modelo estadístico y crear el modelo de predicción de apuestas | Pendiente |

Regla: no se empieza una fase hasta cerrar la anterior, salvo decisión explícita.

---

## Fase 1 — Scraping de estadísticas

### Qué se extrae hoy (diagnóstico del 2026-10-14)

| Fuente | Endpoints usados | Qué se guarda |
|---|---|---|
| Flashscore | calendario `f_*`, `df_st` (stats), `df_su` (incidencias), `df_lu` (alineaciones) | `MatchStats`: 50 campos por equipo y partido (xG, tiros, córners, pases, duelos, tarjetas…), más goles, córners y tarjetas por mitad |
| Sofascore | `/event/{id}/incidents`, `/event/{id}/lineups`, `standings/total`, `top-players/overall`, `seasons` | `PlayerMatchStats`: unos 45 campos por jugador y partido (minutos, tiros, xG, xA, pases clave, regates, duelos, nota…) |
| Winamax | catálogo de cuotas (`PRELOADED_STATE`) | `Odds` + `Snapshot`, **solo los últimos 12 snapshots por liga** (`WINAMAX_SNAPSHOT_RETENTION`); los anteriores se borran |

Además:
- La tabla `Match` solo guarda equipos, fecha, estado y marcador. **No hay árbitro, estadio, jornada ni entrenadores.**
- `scrapers/sofascore.py` ya recibe `formation` y `missingPlayers` en las alineaciones, pero **no se guardan**.

### Bloques de trabajo (orden recomendado)

| # | Bloque | Por qué importa | Esfuerzo | Estado |
|---|---|---|---|---|
| 1 | **Formación y bajas** (lesionados, sancionados) desde `lineups` | Los datos ya llegan y se tiran. Las bajas cambian mucho el λ de goles. | Bajo | ☐ |
| 2 | **Historial de cuotas** (como mínimo apertura y cierre) | Imprescindible para la fase 3: medir el modelo con CLV (*closing line value*). **Cada día sin esto se pierden datos que no se pueden recuperar.** | Bajo-medio | ◐ Tabla `odds_history` + migración `9d4aa151b886` + integración en la ingesta Winamax + tests. Falta: decidir qué pasa con los huecos (`needs_new_row`), programar el refresh cada 6 h y medir el tamaño real |
| 3 | **Contexto del partido**: árbitro, estadio, jornada, entrenadores (`/event/{id}`) | El árbitro es el mejor predictor de tarjetas. | Medio | ☐ |
| 4 | **Stats por periodo de Sofascore** (`/event/{id}/statistics`) | 1ª y 2ª parte de todo, incluidos los córners por mitad (Flashscore los cubre mal). | Medio | ☐ |
| 5 | **Mapa de tiros** (`/event/{id}/shotmap`) | Cada tiro con xG, minuto, situación y parte del cuerpo. Permite modelar goles por tramos y mercados de 1ª parte. | Medio-alto | ☐ |
| 6 | Momentum (`/event/{id}/graph`), H2H | Poco valor; el H2H se puede calcular con datos propios. | Bajo valor | ☐ |

Los endpoints de los bloques 3 a 6 hay que comprobarlos con un partido real antes de programarlos.

### Método para cada bloque

1. Modelo SQLAlchemy (campos nuevos o tabla nueva).
2. Migración Alembic, revisada antes de aplicarla.
3. Parser con test que use un JSON/HTML de ejemplo guardado, **sin peticiones reales**.
4. Integración en `daily_refresh` / `POST /refresh/official`.
5. Medir la cobertura: en cuántos partidos queda relleno cada campo nuevo.

### Decisiones pendientes

- [x] **Historial de cuotas (bloque 2):** ¿qué se guarda? → **Decidido (2026-10-14): opción C.**
  Por confirmar: intervalo X (propuesta: 6 h) y si se guarda solo cuando la cuota cambia (propuesta: sí).
  - **A)** Solo apertura y cierre de cada línea. Ocupa poco y basta para medir CLV.
  - **B)** Todos los snapshots. Permite estudiar cómo se mueven las cuotas, pero la base de datos crece mucho.
  - **C)** Apertura, cierre y un snapshot cada X horas.
- [ ] ¿Qué ligas y competiciones son prioritarias para el histórico?
- [ ] ¿Hasta cuántas temporadas atrás interesa el backfill?

### Requisitos para empezar

- Docker Desktop arrancado (para medir cobertura y probar migraciones).
- Tener en cuenta que `git status` muestra unos 20 archivos modificados sin commitear. Conviene hacer commit antes de empezar la fase 1, para poder separar los cambios.

---

## Fase 2 — UX/UI (pendiente)

Ya detectado:
- 6 páginas existen en `frontend/src/app/pages/` pero no tienen ruta (`analisis`, `calibration`, `match-detail`, `matches`, `recommendations`, `value-bets`).
- Dos estilos de color mezclados: los tokens `brand-*` de `styles.scss` y las clases `slate-*`/`emerald-*` directas.
- Skills previstas: `impeccable`, `make-interfaces-feel-better`, `emil-design-eng`, `dataviz`, `break-ui`.

## Fase 3 — Modelo estadístico y predicción (pendiente)

Ya detectado:
- Base actual: Poisson / Dixon–Coles (`poisson_model.py`), con calibración y backtest ya implementados.
- La evaluación del modelo de apuestas dependerá del historial de cuotas de cierre (fase 1, bloque 2).

## Otros avisos

- Tres versiones de Python: README 3.12+, `.venv` 3.14, `start_bets_ai.bat` 3.10.
- La carpeta `2 DAM/Bets-AI` (con guion) solo contiene una carpeta `.git` vacía; el proyecto es `2 DAM/Bets AI`.
