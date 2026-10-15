# Hoja de ruta — Bets AI

Última actualización: 2026-10-14

| Fase | Objetivo | Estado |
|---|---|---|
| **1** | Scraping de estadísticas mucho más completo | **En curso**. Migración a Flashscore hecha y verificada; backfill en marcha solo (3 refreshes/día). Ahora: **stats por mitad** (ver "Cierre de la fase 1" → reabierta) |
| 2 | Mejorar UX/UI del dashboard | Pendiente (se reabrió la fase 1 el 2026-10-15 antes de empezar) |
| 3 | Mejorar el modelo estadístico y crear el modelo de predicción de apuestas | Pendiente |

Regla: no se empieza una fase hasta cerrar la anterior, salvo decisión explícita.

---

## 🔄 Fase 1 reabierta (2026-10-15): stats por mitad

El usuario decide no pasar aún a la fase 2: interesan los mercados por mitad (**más goles en la
2ª parte**, tarjetas y córners por mitad). Descartado: clasificar el motivo de las bajas.

Diagnóstico (BD tras el refresh 3, 242 filas de `match_stats`):
- `goals_1h/2h`: 100 % y cuadran con el marcador en 240/240. **Ya hecho.**
- `yellow/red_cards_1h/2h`: salen de las incidencias (`df_su`); cuando un equipo no tiene tarjetas
  en una mitad queda `NULL` en vez de `0`.
- `corners_1h/2h`: 0 %. **Pero el feed `df_st` que ya descargamos trae secciones `SE÷Match`,
  `SE÷1st Half`, `SE÷2nd Half` con ~40 stats cada una** (córners, tarjetas, xG, tiros, tiros a
  puerta…). El parser ignora `SE` y se queda con la del partido. Comprobado con 1 petición real
  (Coventry–Newcastle: córners 0/3 + 2/3 = 2/6). Sin peticiones extra para partidos nuevos.

**Decidido (2026-10-15), opción A recortada:** por mitad solo **goles** (ya estaban), **córners**
y **tarjetas** (recuento final de cada mitad). Nada de xG/tiros por mitad. Sin columnas nuevas ni
migración: se rellenan `corners_1h/2h`, `yellow_cards_1h/2h`, `red_cards_1h/2h`.

**Hecho (2026-10-15):**
- `parse_match_statistics` lee las secciones `SE`: totales solo de `Match`; de las mitades solo
  córners y tarjetas; recuento ausente en una sección recibida = 0 (antes quedaba `NULL`).
- Arreglado de paso: una stat que solo aparecía en una sección de mitad se guardaba como total.
- Tarjetas por mitad: manda `df_st` (oficial); `df_su` solo completa (goles por mitad).
- Partidos guardados antes del cambio (`captured_at < PERIOD_STATS_SINCE`, 121) se recargan una
  vez, **solo `df_st`** (1 petición), detrás de los que no tienen stats, con el mismo cupo de 40.
- Tests: 7 del parser por mitad (payload real recortado) + 1 de la cola de recarga. 383 en verde.
- Prueba real: Lecce 3-2 Monza (nuevo) y Coventry 4-1 Newcastle (recargado): mitades = totales
  y goles por mitad = marcador.
- Estimación: ~177 partidos sin stats + 121 recargas → ~8 refreshes (~3 días).

### Props de Winamax ↔ jugadores de Flashscore (hecho 2026-10-15)

- Regla de nombres compartida `player_names_match` (`team_name_matching.py`): palabras en
  cualquier orden, sin tildes, diminutivos como prefijo ("Nico" ~ "Nicolás") y nombres
  incompletos ("Bremer" ~ "Gleison Bremer"). Siempre con **candidato único** en el equipo.
- `find_unique_team_player` (`entity_resolver.py`): la usan el enlazado de Flashscore
  (`PlayerResolver`) y Winamax (`_get_or_create_player`, que ahora busca entre los jugadores de
  los **dos equipos del partido**; antes era nombre exacto global).
- `services/player_merger.py` (CLI, simulación por defecto, `--apply`): fusionó **47 duplicados**
  Sofascore ↔ Flashscore ("Nico Paz" ← "Paz Nicolás"…): stats por partido y cuotas al jugador
  de Sofascore, `flashscore_id` incluido, y rollup de temporada. Copia previa:
  `backups/betsai_antes_fusion_jugadores_2026-10-15.dump`.
- Resultado: los 26 jugadores con props tienen stats de Flashscore (antes 25); 0 pares pendientes.
  Simulado: "Deniz Undav" (Winamax) → "Undav Deniz" (Flashscore).
- Tests: 12 de nombres de jugador + 6 de selección de pares. 401 en verde.
- Límite: un jugador que aparece primero en un prop de Winamax (sin stats aún) se crea sin
  equipo; luego Flashscore lo enlaza por nombre sin orden, pero no por diminutivo.

### Descartado por el usuario (2026-10-15)

- Bloque 3 (árbitro/contexto), bloque 5 (mapa de tiros), bloque 6 (momentum: no apuesta en
  directo), motivo de las bajas. Por mitad: solo goles, córners y tarjetas.

## ✅ Cierre de la fase 1 (2026-10-15) — luego reabierta, ver arriba

Verificado con el refresh 3 (09:27, programado): la regla de bajas no repite los 40 próximos
del refresh anterior (conservan su hora de consulta) y consulta los 21 próximos restantes + 19
jugados del histórico. Jugados pendientes de bajas: 307 → 288. Log con tildes correctas.

**El backfill sigue solo** con la tarea programada. Para ver cómo va: `logs/refresh_AAAA-MM-DD.log`.
Tras el refresh 3 quedaban ~177 partidos sin stats de equipo, ~214 sin stats de jugador y 288
sin bajas.

**Aplazado de la fase 1** (no bloquea la fase 2; retomar cuando toque):
- Bloques 3–6: contexto del partido (árbitro, estadio, jornada), stats por periodo, mapa de
  tiros, momentum. Ver tabla de bloques más abajo.
- ~~Clasificar el motivo de las bajas~~ descartado por el usuario (2026-10-15): poco interés.
- ~~Enlazar jugadores de los *player props* de Winamax con los de Flashscore~~ ☑ hecho (2026-10-15).
- Nombres de jugadores nuevos en orden "Apellido Nombre" (afecta a la UI: ver en fase 2).
- Córners por mitad vacíos con Flashscore.
- ~~Avisos de ruff~~ ☑ 0 (2026-10-15). Queda: diferencias antiguas modelo ↔ BD de `alembic check`
  (defaults de `created_at` y únicos declarados como índice en `app_users`,
  `featured_daily_picks`, `saved_parlays`, `calibration_reports`); no afectan a los datos.
- ~~Borrar `.playwright-mcp/`~~ ☑ (las respuestas reales de Flashscore se movieron a
  `data/raw/flashscore_muestras/`, fuera de git). ~~README/CLAUDE.md~~ ☑ sin Sofascore.
- README: la versión de Python (dice 3.12+, el `.venv` es 3.10) y la lista de páginas siguen
  sin tocar (Python: preguntar; páginas: fase 2).
- **Decidido (2026-10-15):** solo temporada actual (sin backfill de temporadas anteriores) y la
  cobertura de Winamax (~17 partidos por catálogo) no preocupa.

## 📍 Historial de la fase 1 (2026-10-15)

**Objetivo en curso:** sustituir todo lo que venía de Sofascore (bloqueado con `403 challenge`)
por Flashscore. Commiteado por el usuario (`0ae11cf`). **Prueba real pequeña hecha el 2026-10-15**
(ver abajo). 370 tests en verde. Ruff tiene 36 avisos antiguos (imports sin usar, etc.), no de
este trabajo.

### Prueba real pequeña (2026-10-15)

| Paso | Resultado |
|---|---|
| Resultados (feed diario, 2 días) | ☑ 9/9 partidos de Champions del 14/10 cerrados con marcador |
| Histórico de la temporada | ☑ 298 partidos creados, **0 equipos nuevos** (todos enlazados) |
| Stats por equipo (1 partido) | ☑ Coventry 4-1 Newcastle: xG, tiros, posesión, goles por mitad. **Córners por mitad vacíos** (Flashscore no los da por esta vía) |
| Stats por jugador (1 partido) | ☑ Slovan 0-3 Stuttgart: 32 filas. Los jugadores que ya existían se enlazaron (Stiller, Führich…); los 22 nuevos **no existían** (Slovan entero + 6 suplentes de Stuttgart que no venían en los "top players" de Sofascore) |
| Rollup de temporada | 0 filas: el partido era de Champions y las competiciones UEFA no tienen fila en `seasons`. Pendiente verlo con un partido de liga |

**Arreglado:** `FlashscoreStatsIngester.ingest_recent` aplicaba el límite **antes** del filtro top 5,
así que los partidos UEFA ocupaban cupo y se descartaban (con `limit=1` salían 0). Test añadido.

**Ojo:** los jugadores nuevos se guardan con el nombre como lo da Flashscore ("Undav Deniz",
apellido primero). El enlace por id funciona igual, pero en la UI se verán al revés.

**Decidido (2026-10-15): backfill opción b)**, poco a poco con cada refresh (40 partidos de stats
+ 30 de jugadores), para no arriesgar un bloqueo de IP en Flashscore.

**Ventana de stats por jugador ampliada a 60 días** (antes 30) para tener el histórico de **tiros
a puerta por jugador**, que interesa para ese mercado de props. El CLI `daily_refresh` ahora usa
`--stats-limit 40` por defecto (antes `None`: se saltaba el límite y lo bajaba todo de golpe).

**Backfill 1/≈10 (2026-10-15, 189 s, 0 errores):** 40 partidos con stats de equipo, 30 con stats
de jugador (944 filas, **100 % con tiros a puerta y minutos**), rollup 254 filas, 40 partidos de
bajas (359 bajas + 37 dudas). Pendiente: 257 partidos sin stats de equipo (~7 refreshes), 274 sin
stats de jugador (~10), ~328 sin bajas. Ojo: la ventana de 60 días se mueve; 2 partidos del 15/08
ya quedan fuera y cada día que pase sin refresh se escapan más de la 1ª jornada.

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

1. ~~Prueba real pequeña~~ ☑ hecha (2026-10-15).
2. ~~Decisión backfill~~ ☑ opción b). **Programado (2026-10-15):** tarea de Windows
   "Bets AI - Refresh Flashscore" a las 09:00, 15:00 y 21:00 → `refresh_programado.ps1` (comprueba
   Docker, lanza `backend.tools.daily_refresh`, log en `logs/refresh_AAAA-MM-DD.log`). Si el PC está
   apagado, se ejecuta al encenderlo; si Docker Desktop no está abierto, **lo arranca solo** (espera
   hasta 5 min al motor y 2 min a que Postgres esté `healthy`); si aun así falla, lo deja en el log.
   Se decidió **mantener 60 días** de ventana de jugador (no 30): el ahorro sería solo del backfill
   inicial (cada partido se consulta una vez) y 30 días dan muy poca muestra para tiros a puerta.
   Quitar la tarea: `Unregister-ScheduledTask -TaskName 'Bets AI - Refresh Flashscore'`.
   **Refresh 2 (programado, 09:15):** OK, 188 s, 0 errores.
   **Arreglado (idea del usuario):** las bajas de partidos próximos se reconsultaban en cada
   refresh y con 61 próximos (semana europea) el cupo de 40 nunca llegaba al histórico (307
   jugados sin bajas). Ahora un próximo se consulta como mucho una vez cada 20 h
   (`UPCOMING_REFETCH_AFTER`) y los huecos van al histórico: 09:00 → 40 próximos, 15:00 → 21 + 19
   históricos, 21:00 → 40 históricos.
3. ~~Rollup de temporada~~ ☑ funciona (254 filas en el refresh 1).
4. ~~Ventana de stats por jugador~~ ☑ ampliada a 60 días.
5. ~~Verificar resultados del 14/10~~ ☑ 9/9.
6. Borrar la carpeta `.playwright-mcp/` (capturas del navegador; no versionar).
7. Revisar que `README`/`CLAUDE.md` reflejen que Sofascore ya no se usa en el refresh.

### Pendiente aparte

- Clasificar el motivo de las bajas (lesión / sanción / enfermedad / "no cuenta"):
  función de 5–10 líneas que se ofreció a que escriba el usuario.
- ~~Enlazar jugadores de los *player props* de Winamax con los de Flashscore~~ ☑ hecho (2026-10-15).
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
- [x] ¿Qué ligas y competiciones son prioritarias para el histórico? → las actuales (top 5 + UEFA).
- [x] ¿Hasta cuántas temporadas atrás interesa el backfill? → **ninguna**: solo temporada actual.

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

**Sofascore devuelve `403 challenge`** → sustituido todo por Flashscore (fase 1 cerrada el
2026-10-15, ver "Cierre de la fase 1" al principio de este archivo).

## Fase 2 — UX/UI (en curso desde 2026-10-15)

Ya detectado:
- Nombres de jugadores nuevos de Flashscore vienen como "Apellido Nombre" ("Undav Deniz").
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
