# Hoja de ruta — Bets AI

Última actualización: 2026-10-16

| Fase | Objetivo | Estado |
|---|---|---|
| 1 | Scraping de estadísticas mucho más completo | ✅ **Cerrada el 2026-10-16** (última tarea: cuotas Winamax por liga vía WebSocket, 5 días vista). El backfill de stats termina solo (3 refreshes/día) |
| **2** | Mejorar UX/UI del dashboard | **Siguiente** (crítica hecha, nota 13/40; siguiente paso: `/impeccable init`) |
| 3 | Mejorar el modelo estadístico y crear el modelo de predicción de apuestas | Pendiente |

Regla: no se empieza una fase hasta cerrar la anterior, salvo decisión explícita.

---

## 📍 Dónde nos quedamos (2026-10-19, ~20:30), para retomar

Tres agentes en paralelo. Decisiones del usuario: portada solo decorativa; fotos enlazadas al CDN de
Flashscore; una petición a Winamax; sesión de 7 días; rate limit en el login (5/min), en los refresh
y general por IP; dependencias de Python y npm; "todo protegido tras login". **No elegido:** C-4
(PBKDF2 a 600k y `compare_digest`).

- ✅ **Portada decorativa** del dashboard: `pages/dashboard/portada.ts` (campo en SVG en perspectiva,
  entrada de unos 0,9 s solo la primera vez en cada sesión). Build y tests en verde. **Falta verla
  en el navegador** y revisar la frase del subtítulo, que la escribió el agente.
- ✅ **Fotos y cuotas de jugador**: Winamax cambió los ids de mercado. Ahora son 6864 (N+ tiros a
  puerta), 6858 (N+ tiros), **6921 (N+ paradas, nuevo `player_saves_alt`)** y 6933 (tarjeta).
  Muestra en `data/raw/winamax_muestras/`. Fotos en `players.photo_path` (migración `05b07e5715a6`,
  aplicada), servicio `services/player_photos.py`; se rellenan solas con los refreshes. 601 tests.
  Pendiente: comprobar que la variante 15 de la foto no se ve pixelada (`PLAYER_PHOTO_VARIANTS`).
  Las paradas van sin P del modelo (fase 3).
- ✅ **Seguridad: TERMINADA** (estado por id en `AUDITORIA_SEGURIDAD.md` → "Aplicado el 2026-10-19").
  `pip-audit` 0 vulnerabilidades, `npm audit` 0, 601 tests backend + 29 frontend. Comprobado con curl:
  401 sin token, 401 con un token firmado con el secreto antiguo, 429 en el 6.º login. `httpx2` quitado
  (queda `httpx==0.28.1` oficial). Solo queda pendiente C-4 (no elegido) y lo informativo (H-6…H-9, C-13).
  Detalle de cómo estaba a media tarea (histórico):
  - ☑ `JWT_SECRET` escrito en `.env` (nadie lo ha visto); se tocaron `config.py`, `.env.example` y `.gitignore`.
  - ☑ Postgres solo en `127.0.0.1` (`docker-compose.yml`). Falta comprobar que el contenedor se
    recreó y está `healthy`.
  - ☑ `backend/tools/set_password.py` creado; `login.html` ya no muestra `admin/1234`.
  - ☑ `auth.guard.ts`, el interceptor y `app.routes.ts` tocados ("todo tras login").
  - ☑ `deps/rate_limit.py`, `deps/seguridad_http.py` (cabeceras) y `tests/test_seguridad_api.py` nuevos.
  - ☑ `requirements.txt` subido: fastapi 0.149.0, PyJWT 2.15.1, curl_cffi 0.16.3.
  - ⚠️ **Revisar `httpx2==2.14.0` en `requirements.txt`**: el TestClient usa `httpx`, no `httpx2`;
    podría ser un typosquat. Se le pidió al agente que lo cambie por `httpx` y lo desinstale. Si
    sigue ahí: `pip uninstall -y httpx2` y poner `httpx`.
  - ☐ Sin confirmar: `npm update` / `ng update` y `npm audit`, `pip-audit` final, pruebas con curl
    (401 sin token, 429 en el 6.º login) y la sección "Aplicado el 2026-10-19" de
    `AUDITORIA_SEGURIDAD.md`. Si no está, retomar desde ahí.
  - **Lo que tiene que hacer el usuario al volver:** `.\.venv\Scripts\python.exe -m backend.tools.set_password admin`
    (hasta entonces sigue siendo `1234`; mínimo 10 caracteres), reiniciar la API (la de :8000 seguía con el
    código viejo) y volver a iniciar sesión.
  - Avisos: detrás del proxy de Angular todo llega desde 127.0.0.1, así que el límite de 120/min es en la
    práctica global; un refresh que falla gasta igualmente su cupo de 3 min; `docker compose up -d` falló una
    vez con "port 5432 not available" y al reintentar fue bien (si le pasa a la tarea programada, se salta ese refresh).
- Refresh del 19/10 a las 19:13 (recuperó lo perdido del 17 y el 18): OK, 0 errores de Flashscore,
  77 partidos con cuotas. A las 21:00 quedaban 45 partidos de bajas en cola; los recoge la tarea
  programada.
- No hay nada commiteado: conviene hacer commit al volver (hay mucho cambio sin guardar).

---

## ✅ Hecho (cerró la fase 1): cuotas de Winamax por liga vía WebSocket

**Anotado el 2026-10-15 a mediodía; el usuario lo retoma esa tarde.**

### El problema

Winamax **sí tiene cuotas** de partidos de dentro de varios días (p. ej. el 15/10 ya había
cuotas en su web de partidos del 18/10), pero en el dashboard, al pulsar uno de esos partidos,
**no sale ninguna cuota**. Hoy solo sabemos descubrir partidos por dos vías y ninguna llega:

| Vía | Qué trae | Límite |
|---|---|---|
| HTML de cualquier página (`PRELOADED_STATE`) | ~50 eventos de la portada (~20 de fútbol) | Selección fija de Winamax |
| Socket `calendar:24` | Todos los partidos de las **próximas 24 h** (590; 120 de fútbol) | Solo 24 h |

Además, el refresh solo intenta los partidos de la BD de **hoy y mañana** (`--days-ahead 2` en
`refresh_programado.ps1` y `days_ahead=2` por defecto en `POST /refresh/odds`).

### Objetivo

Pedir por el socket **la lista de partidos de cada liga que seguimos** (top 5 + UEFA), igual que
hace la web al abrir la página de una competición, para tener todos los partidos con cuotas
publicadas aunque sean de dentro de 3–7 días. Después, ampliar `days_ahead` a 7.

### Ids de Winamax ya identificados (fútbol, de `categories`/`tournaments` de la portada)

| Liga | País (`categoryId`) | Torneo (`tournamentId`) |
|---|---|---|
| Premier League | 1 (Inglaterra) | 1 |
| LaLiga | 32 (España) | 36 |
| Bundesliga | 30 (Alemania) | 42 (ojo: 29 es la de **Austria**, cat. 17) |
| Serie A | 31 (Italia) | 33 |
| Ligue 1 | 7 (Francia) | 4 |
| Champions League | 800000542 (Europa) | 151665 (comprobar: la 217 de la cat. 800000485 parece de otro deporte) |
| Europa League | 800000542 (Europa) | 10909 |
| Conference League | 800000542 (Europa) | 151677 |

URL web de una competición: `/apuestas-deportivas/sport/1/<categoryId>/<tournamentId>`. Su
HTML **no** trae los partidos de la liga (devuelve los mismos 50 de la portada; comprobado con la
Europa League), por eso hay que ir al socket.

### Lo que ya sabemos del socket (sacado del bundle `main.*.js` de su web)

- Servidor: `wss://sports-eu-west-3.winamax.es/uof-sports-server/socket.io/?language=ES&embed=false&EIO=3&transport=websocket`
  (Socket.IO v2, sin autenticación). Implementado en `scrapers/winamax.py`
  (`_fetch_socket_route(route)`, `fetch_calendar`, `parse_socketio_event`).
- Protocolo: el servidor envía `0{…}` y `40`; se pide con `42["m",{"route":"…","requestId":"…"}]`;
  la respuesta llega como `42["m",{…,"requestId":"…"}]`; `2` → contestar `3` (ping/pong).
- Rutas confirmadas: `calendar:24` (funciona), `match:<matchId>` (la usa la página de partido),
  `history` (apuestas del usuario, requiere sesión).
- En el bundle, los menús usan claves `sport:1`, `sport:1-category:32`,
  `sport:1-category:32-tournament:36`. **No está confirmado** que sean rutas del socket.

### Cómo continuar

1. Buscar en el bundle (y en los chunks `./index.*.js` que carga bajo demanda, la página de
   competición puede estar en uno de ellos) dónde la página `/sports/:sportId/:categoryId/:tournamentId`
   hace `jr.send({route: …})`. Pistas: el `ASK_BET_FILTER_ROUTE` (`r={route:a}`) y quien llama
   a `menuSetKey`. Candidatas a probar: `tournament:36`, `sport:1-category:32-tournament:36`,
   `category:32`, `sport:1`.
2. Probar **1 conexión** con la ruta candidata para una liga (p. ej. LaLiga) y guardar la
   respuesta. Comprobar que `parse_football_matches` la lee y que trae partidos de >24 h.
3. Implementar `fetch_tournament(tournament_id)` y en el ingester pedir las 8 ligas de la tabla
   (8 mensajes por la misma conexión o 8 conexiones cortas), unirlas con
   `merge_winamax_matches` y mantener la caída a home + calendario si falla.
4. Subir `days_ahead` a 7 en `refresh_programado.ps1` y en `POST /refresh/odds`, y medir el
   tiempo del botón (hoy ~30 s con 36 partidos; con 7 días pueden ser 80–100 partidos → valorar
   saltar los actualizados hace < 15 min, propuesta pendiente de respuesta del usuario).
5. Tests con una respuesta real recortada (como `CALENDAR_FRAME` en `test_winamax_matching.py`).

Si `calendar:<horas>` acepta más de 24 (p. ej. `calendar:168`), sería una alternativa más
simple: probarlo también (no se ha probado; la web solo usa 24).

### Estado (2026-10-16): hecho y verificado (el usuario pulsó "Actualizar cuotas": funciona)

- **Ruta confirmada** por el usuario en DevTools: al abrir LaLiga la web envía
  `42["m",{"route":"tournament:36","requestId":"…"}]` y la respuesta trae `matches`, `bets`,
  `outcomes`… (mismo formato que `PRELOADED_STATE`). La URL del socket lleva además
  `version=3.71.0` (`SOCKET_CLIENT_VERSION`). Claude no pudo probarla: el clasificador de permisos
  bloquea descargar el bundle y conectarse al socket desde la sesión.
- `scrapers/winamax.py`: `SocketRouteCollector` (protocolo sin red, testeado) +
  `_fetch_socket_routes` (todas las rutas por **una** conexión) + `fetch_tournaments(ids)`.
- Ingester: `WINAMAX_TOURNAMENT_IDS` (sport_key → torneo); `_socket_catalog` une home +
  ligas con partidos en la ventana + `calendar:24`; si una fuente falla sigue con las demás.
  Log por liga: `Winamax por liga (Socket.IO): {liga: "N hasta dd/mm"}` y aviso si una liga
  con partidos en BD llega vacía (para detectar un id de torneo mal puesto).
- **5 días** en `refresh_programado.ps1` (`--days-ahead 5`) y en `POST /refresh/odds`.
- Botón: `skip_fresh_minutes=15` → no descarga los partidos con cuotas frescas según
  `needs_odds_refresh` (**la escribe el usuario**); conservan sus cuotas por el arrastre del
  snapshot anterior. El dashboard muestra "· N ya al día".
- **Probado contra Winamax (2026-10-16, 1 conexión, 0,4 s): 8/8 ligas responden** y los 8 ids
  son correctos (Champions 151665, Europa League 10909 y Conference 151677 confirmados). Cada liga
  trae ~2 jornadas: Premier 21, LaLiga 22, Bundesliga 19, Serie A 21, Ligue 1 19 partidos (desde
  hoy), Champions 18 (desde el 20/10), Europa League 18 y Conference 10 (desde el 22/10). Además
  llegan 1 "MultiFútbol <liga>" (torneo 9000068xx) y 1 entrada de ganador de la competición
  (inicio a final de temporada) por liga; no emparejan con la BD y se ignoran.
- `needs_odds_refresh`: nunca leído → se descarga; si empieza en < 2 h, margen de 5 min; si no,
  15 min. 444 tests en verde.

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
  - Se mantiene `MAX_STORED_ODDS` (desde 2026-10-17, no se guardan cuotas > 10,00).
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

## Cuotas Winamax de los partidos del día (2026-10-15)

El usuario necesita como mínimo las cuotas de los partidos que se juegan hoy.

- **Diagnóstico:** el HTML de Winamax (`PRELOADED_STATE`) solo trae los ~50 eventos de su
  portada (22 de fútbol), **pida la página que se pida** (comprobado con la de la Europa League:
  `/sport/1/800000542/10909` devuelve los mismos ids; el estado dice 20 partidos y trae 13). El
  resto lo carga la web por Socket.IO. Además se perdían partidos que sí llegaban por los
  nombres en español ("Marsella", "Friburgo", "Sparta Praga", "Lillestrøm").
- **Hecho:** segundo intento de emparejado por hora de inicio (±10 min) + `team_side_score`
  (2 = mismo club, 1 = palabra distintiva o raíz de 4 letras; ignora "real", "united"…) con
  ganador único; "ø/æ/ß…" normalizados. Portada del día: 11 → 17 emparejados, 0 errores.
  Tests en `test_winamax_matching.py`. 416 en verde.
- **Hecho:** la tarea programada (09/15/21 h) también trae las cuotas de **hoy y mañana**
  (`import_winamax_props --days-ahead 2 --all-leagues`): ~1 + N partidos peticiones. De paso da
  tres tomas diarias para el historial de cuotas (CLV en la fase 3).
- **Resultado con solo el HTML:** Europa League 8 → 13 de 18 y Conference 2 → 3 de 18.
- **Hecho — Socket.IO de Winamax** (el usuario lo considera un mínimo indispensable):
  - Protocolo sacado del bundle de su web (`main.*.js`): Socket.IO v2 (`EIO=3`) en
    `wss://sports-eu-west-3.winamax.es/uof-sports-server/socket.io/`, sin autenticación; se emite
    `42["m", {"route": "calendar:24", "requestId": …}]` (lo que hace su página "Calendario") y
    responde con los partidos de las próximas 24 h de todos los deportes (590; 120 de fútbol)
    en el mismo formato que `PRELOADED_STATE`.
  - `scrapers/winamax.py`: `fetch_calendar()` + `parse_socketio_event()`; el ingester une home +
    calendario (`merge_winamax_matches`) y, si el socket falla, sigue solo con la home.
  - 1 s de pausa entre descargas de detalle de partido (ahora son ~40 por corrida).
  - `websockets==16.1.1` añadido a `requirements.txt`.
  - **Resultado: 36 de 36 partidos de hoy con cuotas** (EL 18/18, ECL 18/18), 4.657 cuotas
    (584 de jugadores), 57 s. Los de liga del viernes/sábado entran en los refreshes de las
    15:00 y 21:00 (el calendario cubre 24 h). Tests: `test_winamax_matching.py`. 428 en verde.
- **Hecho — tope de `/dashboard/graded-odds`** (4.000 por defecto): antes cortaba por orden de
  recorrido; ahora, si sobra, se quedan las de mayor EV (`keep_best_lines`). Calificar las 4.657
  tarda ~1 s.
- **Para la fase 3:** hoy **ninguna** línea de Europa League/Conference tiene P del modelo (0 de
  4.657): el Poisson usa las stats de temporada de la liga y las competiciones UEFA no tienen.
- Incidencia: la API (arrancada con `py -3.10` desde `start_bets_ai.bat`, **no** el `.venv`) se
  quedó colgada tras una recarga automática a mitad de petición; hubo que reiniciarla.
  Causa (confirmada la 2ª vez): con `--reload`, al cambiar un `.py` uvicorn deja de aceptar
  peticiones y **espera a que termine la que está en curso**; si es "Actualizar jornada"
  (~4 min), toda la app parece desconectada de la BD durante ese tiempo.

### Botón del dashboard: "Actualizar jornada" → "Actualizar cuotas" (2026-10-15)

- Queja del usuario: "antes era instantáneo". Antes no había backfill pendiente y Winamax daba
  ~7 partidos; hoy el botón lanzaba el pipeline completo (stats 40 + jugadores 30 + bajas 40 +
  Winamax 36) ≈ **4 min**, bloqueando "Recargar partidos".
- Hecho: `POST /refresh/odds` (solo Winamax, hoy y mañana, top 5 + UEFA) y el botón lo usa. Las
  stats las trae la tarea programada. El resultado se muestra junto al botón ("Cuotas
  actualizadas a las 12:30 · 36 partidos", o el error) en vez de tragarse los fallos, y la lista
  de partidos sigue usable mientras tanto. Helper `fmtTime` en `core/format.ts`.
- Descargas de detalle en paralelo para el botón (`fetch_workers=4`); la tarea programada sigue
  de una en una con 1 s de pausa. Guardado en BD siempre en serie (la sesión no es multihilo).
- Medido con 36 partidos: **4 min → 29–34 s**. Desglose: páginas de partido 16,9 s (~1,3 s
  cada una, son pesadas), socket 7 s (~1 MB), BD 3,7 s, portada 1,2 s.
- Frontend: compila. `app.spec.ts` tiene 2 tests que **ya fallaban antes** (falta configurar el
  router en el test); pendiente de arreglar.

## Fase 2 — UX/UI (en curso desde 2026-10-15)

### Bloque 1 (2026-10-16/17): Partidos + página de partido + boletín ✅

Proceso Impeccable completo: `init` (PRODUCT.md), `shape` (brief confirmado), ronda de dirección
visual (elegida **"Grafismo de retransmisión"**; contrato en
`.impeccable/surfaces/frontend-src-app-pages-match-detail.md`), construcción, dos pasadas de capturas
(1440 y 390 px, `.impeccable/review/`), detector sin hallazgos y revisión final independiente
(dos rondas de correcciones: 8 + 3). El sistema visual queda en **`DESIGN.md`** (y
`.impeccable/design.json`): tokens `tv-*`, reglas y componentes para migrar el resto de páginas.

- **Portada (`/dashboard`)**: solo partidos de los próximos 5 días, por día (abre en el primero) y
  competición, con escudo y 1X2 de Winamax; cada cuota va al boletín con un clic.
- **Partido (`/partido/:id`, sobre `match-detail`)**: marcador con los colores de los dos clubes y
  5 pestañas: Previa (onces en perspectiva, medias en su campo, forma, bajas, mercados clave),
  Alineaciones, Estadísticas (casa/fuera o temporada, por mitades, últimos 5), Cuotas (todas las
  líneas en lenguaje llano con P del modelo y EV; totales en tabla Más/Menos) y Jugadores (por 90' y
  sus cuotas de goleador). Hueco "Mejores cuotas · próximamente" reservado para la fase 3.
- **Boletín lateral** en todas las páginas (`BetslipService`, `localStorage`), guarda en Mis apuestas.
- **Backend nuevo**: `GET /matches/{id}/preview` (`match_preview.py`), `with_odds` en `/matches`
  (`match_listing.py`, 1X2 con nombres de Winamax en español emparejados), `match_id` en
  `/dashboard/graded-odds`, escudos + colores de club (`team_crests.py`, Pillow, tabla
  `CLUB_COLOR_OVERRIDES` revisada a mano), alineación oficial bajo demanda (`match_lineups.py`,
  tabla `match_lineups`). Migración `e4e49e99c5f9`. CLI `backfill_team_crests`.
- `df_lu` (alineaciones de Flashscore) **ya no existe** (devuelve `0`): la oficial sale de `epmsse`
  (`inBaseLineup`). Verificado el 2026-10-17 con Everton–Chelsea a 31 min del inicio: los dos
  onces oficiales (11 + 11, con posición) llegan bien.
- Tests: 481 backend + 17 frontend (los 2 de `app.spec.ts` que fallaban, arreglados).

Pendiente de este bloque / siguientes:
- ✅ (2026-10-17) Tope de cuotas: `MAX_STORED_ODDS` sube a 10,00 (se guardan y se ven) y
  `MAX_RECOMMENDABLE_ODDS = 4,00` (`value_calculator.py`) impide que una cuota mayor llegue a
  value bets, recomendaciones, combinadas, picks destacados o a la marca "con valor" de las
  graded odds (`recommendable` en cada línea). Primer refresh: 11.846 cuotas (antes 6.473).
- Nombres de Flashscore "Apellido Nombre" (ya estaba detectado).
- ✅ (2026-10-21) Migrar al mundo nuevo: Mis apuestas, Transparencia y Login (tokens `tv-*`).
  - **Login**: rótulo "Bets [AI]" con la entrada de la portada; el PNG solo queda en `public/brand`
    (favicon nuevo `favicon.svg`).
  - **Portada**: balón de estrellas propio (no el logotipo de la UEFA) que entra rodando hasta el
    punto central; su posición se proyecta con la misma perspectiva del campo (`posicionBalon`).
  - **Estadísticas eliminada** (página, ruta, enlace, `GET /statistics/*` y sus schemas): las
    estadísticas viven en cada partido.
  - **Mis apuestas**: importe al guardar desde el boletín (simples y combinadas), beneficio, ROI y
    % de acierto; corrección manual ganada / perdida / nula (`PATCH /me/parlays/{id}`) que manda
    sobre la automática. Migración `333abb2b3059` (`stake`, `manual_result`).
  - **Transparencia**: solo los picks diarios del modelo (`featured_daily_picks`): acierto, racha,
    picks de hoy e histórico por día. Las reglas de selección se sustituyen en la fase 3.
  - Beneficio (`beneficio_apuesta`): ganada = importe × (cuota − 1), perdida = −importe, nula = 0 €
    (fuera de lo apostado del ROI); pendientes, "a revisar" y sin importe no cuentan.
- Páginas sin ruta que siguen ahí: `matches`, `value-bets`, `recommendations`, `analisis`, `calibration`.

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
