# Hoja de ruta — Bets AI

Última actualización: 2026-10-14

| Fase | Objetivo | Estado |
|---|---|---|
| **1** | Scraping de estadísticas mucho más completo | **En curso**: bloques 1 y 2 hechos; migrando todo de Sofascore a Flashscore (ver "Dónde nos quedamos") |
| 2 | Mejorar UX/UI del dashboard | Pendiente |
| 3 | Mejorar el modelo estadístico y crear el modelo de predicción de apuestas | Pendiente |

Regla: no se empieza una fase hasta cerrar la anterior, salvo decisión explícita.

---

## 📍 Dónde nos quedamos (2026-10-14, tarde)

**Objetivo en curso:** sustituir todo lo que venía de Sofascore (bloqueado con `403 challenge`)
por Flashscore. **Código hecho y en verde (369 tests, ruff OK), pero aún sin probar de punta a
punta con datos reales.** Nada commiteado desde el último commit del usuario.

### Hecho

| Pieza | Archivo | Probado con datos reales |
|---|---|---|
| Resultados por id de Flashscore (paso 2) | `services/flashscore_results_updater.py` | ☐ No (no había partidos terminados) |
| Clasificación → `team_season_stats` (paso 3) | `scrapers/flashscore.py` (`fetch_league_page`, `parse_standings`) + `services/flashscore_standings_ingester.py` | ☑ 5 ligas, 96 equipos, 0 duplicados. **Ya está cargada la de hoy** (el modelo ya no usa la del 17/09) |
| Histórico de la temporada (paso 3) | `services/season_history_ingester.py` | ☐ No ejecutado aún |
| Stats post-partido por id (paso 4) | `services/flashscore_stats_ingester.py` (`_ingest_one`) | ☐ |
| Stats por jugador (paso 5) | `scrapers/flashscore.py` (`fetch/parse_player_match_stats`, GraphQL `epmsse` + `epmsd`) + `services/flashscore_player_stats_ingester.py` | Parser sí (con respuesta real guardada); ingester no |
| Totales de temporada por jugador (paso 5) | `services/player_season_rollup.py` | ☐ |
| Resolución de equipos/jugadores por id de Flashscore → nombre → aproximado | `services/entity_resolver.py`, `services/team_name_matching.py` | Equipos sí |
| Pipeline solo Flashscore (6 pasos, límite 40 partidos/refresh en stats) | `services/daily_refresh.py`, `tools/daily_refresh.py`, `app/api/refresh.py` | ☐ |
| Migración `af9813f29f47`: `teams.flashscore_id`, `players.flashscore_id`, `matches.player_stats_fetched_at` | aplicada en local | ☑ |

Endpoints de Flashscore descubiertos (todos comprobados con una petición real):
- Clasificación: HTML `football/<país>/<liga>/standings/` (ids `tournamentId`/`tournamentStageId`,
  temporada y **todos los partidos jugados de la temporada**) + feed `to_{torneo}_{fase}_1`.
- Stats por jugador: `https://13.ds.lsapp.eu/pq_graphql?_hash=epmsse&eventId=…&projectId=13`
  (jugadores) y `?_hash=epmsd&eventId=…&providerId=7` (103 stats + notas, ~1 MB).
- Bajas: `?_hash=dmpe2&eventId=…&projectId=13`.

### Siguiente paso (en este orden)

1. **Prueba real pequeña:** ejecutar el histórico (crea ~298 partidos jugados, 0 peticiones
   extra) y las stats de **1** partido (equipo + jugadores, 4 peticiones). Comprobar que los
   jugadores de Flashscore se enlazan con los existentes (nombre en orden inverso: "Gordon
   Anthony" ↔ "Anthony Gordon") y que el rollup de temporada sale bien.
2. **Decisión del usuario — backfill de stats de esos ~298 partidos (~1.200 peticiones):**
   - a) de golpe con un CLI (~30–40 min), o
   - b) poco a poco: cada refresh procesa 40 partidos (stats) + 30 (jugadores) → ~8 refreshes.
3. Verificar los **resultados de esta noche** (Champions/Europa del 14/10) tras el refresh.
4. Borrar la carpeta `.playwright-mcp/` (capturas del navegador; no versionar).
5. Revisar que `README`/`CLAUDE.md` reflejen que Sofascore ya no se usa en el refresh.

### Pendiente aparte

- Clasificar el motivo de las bajas (lesión / sanción / enfermedad / "no cuenta"):
  función de 5–10 líneas que se ofreció a que escriba el usuario.
- Enlazar jugadores de los *player props* de Winamax con los de Flashscore (hoy por nombre exacto).
- Bloque 3 (árbitro, estadio, jornada) — desde Flashscore.
- Diferencias antiguas modelo ↔ BD que detecta `alembic check` (`app_users`,
  `featured_daily_picks`): no tocadas.

---

## Fase 1 — Scraping de estadísticas

### Qué se extraía (diagnóstico inicial del 2026-10-14, ya superado: ahora todo es Flashscore)

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
| 1 | **Bajas** (lesionados, sancionados) desde `lineups` | Los datos ya llegan y se tiran. Las bajas cambian mucho el λ de goles. **Decidido (2026-10-14): solo bajas, la formación no se guarda** (el modelo no sacaría partido a "más defensiva/ofensiva"). | Bajo | ☑ Verificado tras el reset: 40 partidos, 337 bajas + 31 dudas, 0 errores. Ojo: el motivo más común es "Fuera de la plantilla" (109) e "Inactivo" (27), que no son lesiones. Fuente: **Flashscore** (GraphQL `pq_graphql?_hash=dmpe2`, no `df_lu`, que está vacío hasta 1 h antes). Trae bajas (`missingPlayers`) y dudas (`unsureMissingPlayers`), días antes y también de partidos jugados. Tabla `match_absences` + `matches.absences_fetched_at` (migración `3ab0506e291c`) + paso 5 del refresh + tests. Falta: un refresh real y medir cobertura. Límite: los 162 partidos jugados con id de Sofascore no tienen id de Flashscore, así que no entran en el histórico |
| 2 | **Historial de cuotas** (como mínimo apertura y cierre) | Imprescindible para la fase 3: medir el modelo con CLV (*closing line value*). **Cada día sin esto se pierden datos que no se pueden recuperar.** | Bajo-medio | ☑ Tabla `odds_history` + migración `9d4aa151b886` + integración en la ingesta Winamax + tests. Verificado con 2 refreshes reales (2026-10-14, 6 partidos de Champions): ~133 líneas/partido, 236 B/fila; en el 2º, 749 líneas extendidas y 40 cambios reales (todos en un partido). Arreglado de paso: Par/Impar y "mitad con más goles" se guardaban todos como `Under`. Estimación: ~360 MB por temporada de 10 ligas |
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
  - Se guarda solo cuando la cuota cambia respecto al refresh anterior.
  - Refresh **manual**, sin programar: el "cada X horas" lo marca el usuario al lanzarlo.
  - Si una línea falta en el refresh anterior del partido y vuelve, abre fila nueva aunque
    tenga la misma cuota (el intervalo no cubre el hueco).
  - Se mantiene `MAX_STORED_ODDS` (no se guardan cuotas ≥ 4,00).
  - **A)** Solo apertura y cierre de cada línea. Ocupa poco y basta para medir CLV.
  - **B)** Todos los snapshots. Permite estudiar cómo se mueven las cuotas, pero la base de datos crece mucho.
  - **C)** Apertura, cierre y un snapshot cada X horas.
- [ ] ¿Qué ligas y competiciones son prioritarias para el histórico?
- [ ] ¿Hasta cuántas temporadas atrás interesa el backfill?

### Requisitos para empezar

- Docker Desktop arrancado (para medir cobertura y probar migraciones).
- Tener en cuenta que `git status` muestra unos 20 archivos modificados sin commitear. Conviene hacer commit antes de empezar la fase 1, para poder separar los cambios.

---

### Reset de datos (2026-10-14)

Se vació la BD de datos scrapeados para empezar limpio (copia previa en
`backups/betsai_antes_reset_2026-10-14.dump`, restaurable con `pg_restore`). Se conservan
usuarios, apuestas guardadas, ligas, mercados y bookmaker. Motivos:

- El calendario Flashscore metía en nuestras ligas Premier League 2/U18, LaLiga2,
  Bundesliga/Serie A femeninas y Champions de Asia/África. Corregido: nombre exacto de
  competición (`resolve_fixtures_sport_key`).
- Los equipos se duplicaban entre fuentes ("Bayern Munich" vs "FC Bayern München") porque el
  calendario buscaba por nombre exacto. Corregido: `team_name_matching.py` empareja con los
  equipos de Sofascore de la liga antes de crear uno nuevo.

Reconstrucción: clasificaciones Sofascore del **17/09** (JSON guardados; hoy Sofascore
devuelve **403 "challenge"**) + refresh oficial. El histórico de partidos jugados empieza hoy.

Resultado del primer refresh: 112 partidos (top-5 + UEFA), **0 equipos duplicados en top-5**
(los 69 equipos nuevos son clubes de fuera del top-5 en competiciones UEFA), 7 partidos con
cuotas Winamax (limitación previa: el overview HTTP solo trae ~17 partidos).

**Sofascore devuelve `403 challenge`** → se está sustituyendo todo por Flashscore. Estado
detallado en **"📍 Dónde nos quedamos"**, al principio de este archivo.

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

- Python: el `.venv` se recreó con **3.10** el 2026-10-14 (el anterior era 3.14 con paquetes de 3.12
  y no arrancaba). Coincide con `start_bets_ai.bat`. El README sigue diciendo 3.12+.
- La carpeta `2 DAM/Bets-AI` (con guion) solo contiene una carpeta `.git` vacía; el proyecto es `2 DAM/Bets AI`.
