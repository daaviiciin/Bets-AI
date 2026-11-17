# Propuesta: chat con los datos de Bets AI (MCP)

> Estudio de viabilidad y diseño inicial, 2026-11-13. **No se ha implementado nada.**
> Los precios y modelos salen de la referencia de la API de Claude con fecha 2026-11-03 (ver
> [Fuentes](#fuentes)). Lo que no he podido comprobar va marcado con *(sin verificar)*.

## Resumen

- **Es viable y lleva poco trabajo** si se hace como **servidor MCP local (stdio)** que lee la BD con
  los `services/` que ya existen y se usa desde **Claude Code o Claude Desktop** con la suscripción que
  ya tienes. No hace falta clave de API, no hay coste extra y no se abre ningún puerto. El mínimo útil sale en una tarde.
- **El chat dentro de la app (Angular + API de Claude)** también es viable, pero cuesta dinero por uso
  (de ≈ 0,01 $ a 0,35 $ por conversación, según el modelo), necesita un panel nuevo con streaming y hay
  que mantener el bucle de herramientas. Tiene sentido **después**, y solo si quieres enseñarlo dentro de
  la propia app en el portfolio.
- **Recomendación: opción C por fases.** Una sola capa de herramientas (`backend/chat/`), que primero se
  sirve como MCP local (A). El chat interno (B) queda como fase opcional sobre esas mismas funciones.

---

## 1. Opciones reales y comparación

### A. Servidor MCP propio (local), usado desde Claude Code / Claude Desktop

Un proceso Python (`python -m backend.tools.servidor_mcp`) con el SDK oficial `mcp` (2.3.0 en PyPI; en
la 2.x, `FastMCP` pasa a llamarse `MCPServer`) que expone de 8 a 10 herramientas de **solo lectura**.

| Aspecto | Detalle |
|---|---|
| Transporte | **stdio**: el cliente (Claude Code o Desktop) lanza el proceso y se comunica con él por stdin/stdout. Nada escucha en la red. El transporte HTTP (*streamable HTTP*) solo hace falta para clientes remotos (claude.ai web y el conector MCP de la API). |
| Acceso a datos | **Directo a los `services/` con una sesión de BD** (`get_session_factory()`), sin llamar a la API con un token. Mismo proceso y misma máquina: no hay JWT ni hace falta tener uvicorn arrancado; basta con PostgreSQL. El `.env` se busca por ruta absoluta (`config.py` → `PROJECT_ROOT / ".env"`), así que funciona sea cual sea el directorio desde el que se lance el proceso. |
| Convivencia con FastAPI | No se tocan: son dos entradas independientes que comparten `services/` y modelos. El servidor MCP **no** añade endpoints a la API. |
| Clientes | Claude Code: `.mcp.json` en la raíz del repo (ámbito *project*) o `claude mcp add` (ámbito *local*). Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json`, con `command` apuntando al `python.exe` del `.venv`. **claude.ai web no** funciona con stdio: sus conectores se conectan desde la nube y necesitan una URL HTTPS pública *(sin verificar en la documentación de claude.ai; es lo que exige el conector MCP de la API)*. |
| Coste | 0 € extra. Gasta del cupo de tu plan de Claude, como cualquier otra conversación. |

### B. Chat dentro de la app (panel Angular + endpoint FastAPI con streaming)

Un `POST /chat` (SSE) que llama a la API de Claude con *tool use* sobre las mismas funciones, y un panel
lateral en Angular que va pintando el texto a medida que llega.

| Aspecto | Detalle |
|---|---|
| SDK | `anthropic` (2.8.0 en PyPI). El bucle se hace con el **Tool Runner** (`client.beta.messages.tool_runner` + `@beta_tool`) o a mano; el streaming, con `client.messages.stream(...)`. |
| Clave | `ANTHROPIC_API_KEY` en `.env`, leída solo a través de `config.py` (`get_settings()`). |
| Conector MCP de la API | **No encaja aquí**: solo funciona con servidores **públicos por HTTP** (ni stdio ni localhost), está en beta y solo admite *tools*. Para esta app local habría que publicar el servidor con un túnel, lo que añade superficie de ataque sin ganar nada. Si se quiere reutilizar el servidor MCP desde el chat interno, el SDK de Python trae los helpers `anthropic.lib.tools.mcp` (`async_mcp_tool`), que conectan con un servidor stdio local desde tu propio proceso. |
| Agent SDK | Descartado: es el entorno de Claude Code (Bash, ficheros, web). Para un chat de solo lectura sobre 10 funciones sobra y añade riesgo. |
| Coste | Por uso: ver la tabla siguiente. |

**Coste estimado de una conversación típica**: 4 preguntas y 6 llamadas a herramientas, es decir,
~10 peticiones a la API. Supongo un prefijo fijo de ~5k tokens (*system* más definiciones de
herramientas), resultados compactos de ~1,5k tokens y ~1k tokens de salida por petición, razonamiento
incluido. En total, ~110k tokens de entrada procesados y ~10k de salida.

| Modelo | Entrada / salida ($/MTok) | Lectura de caché ($/MTok) | Sin caché | Con caché de prompts | 60 conversaciones/mes (con caché) |
|---|---|---|---|---|---|
| Claude Haiku 5.5 (`claude-haiku-5-5`) | 0,10 / 0,50 | ~0,01 *(sin verificar: supongo 0,1×)* | ~0,016 $ | **~0,01 $** | **~0,6 $** |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | 2 / 10 | 0,10 | ~0,32 $ | **~0,17 $** | **~10 $** |
| Claude Opus 5.5 (`claude-opus-5-5`) | 4 / 20 | 0,20 | ~0,64 $ | **~0,34 $** | **~20 $** |

Notas:
- La escritura en caché cuesta 1,25× la entrada (TTL de 5 min). Con caché, la salida pasa a ser la mayor
  parte del coste.
- Con conversaciones largas o resultados grandes (por ejemplo, volcar las 4.000 líneas del dashboard) el
  coste se multiplica. Por eso las herramientas devuelven resúmenes y no tablas enteras.
- Haiku 5.5 tiene ese precio para prompts de hasta 100k tokens; por encima, 0,50 / 2,50 $.
- Son órdenes de magnitud, no mediciones: no he lanzado ninguna petición.

### C. Híbrido: una capa de herramientas, dos salidas

```
backend/chat/herramientas.py     funciones (session, args) -> dict compacto; reutilizan services/
backend/tools/servidor_mcp.py    MCPServer stdio que registra esas funciones   (fase A)
backend/app/api/chat.py          POST /chat: tool runner con @beta_tool sobre las mismas (fase B, opcional)
```

El chat interno usaría el bucle del SDK con las funciones en el mismo proceso, no el conector MCP (por
lo explicado en B).

### Comparación

| | A. MCP local | B. Chat en la app | C. Híbrido (A ya, B luego) |
|---|---|---|---|
| Esfuerzo | **1 tarde** el mínimo + 2–3 tardes el catálogo completo | 4–6 tardes (endpoint SSE, panel Angular, bucle, tests, avisos) | A + B, pero B reutiliza todo |
| Coste mensual | 0 € | ~0,6 $ (Haiku) / ~10 $ (Sonnet) / ~20 $ (Opus) con uso moderado | 0 € hasta que se active B |
| Seguridad | Muy buena: stdio, nada en red, solo lectura | Clave de API en `.env`; endpoint con JWT y límite de peticiones; la inyección a través de los datos llega a un modelo que habla en tu propia UI | Igual que A; B hereda las mismas herramientas |
| Valor para el portfolio | Medio-alto (MCP está de moda y es fácil de enseñar en un vídeo o un gif) | Alto (se ve dentro del producto) | El más completo: una «capa de herramientas reutilizable» es un buen argumento de arquitectura |
| Mantenimiento | Bajo: si cambian los services, se ajustan los tests de la capa | Medio: SDK, modelos que se retiran, UI de chat, costes | Bajo hasta que llegue B |

---

## 2. ¿Merece la pena aquí?

### Dónde aporta un chat algo que el dashboard no da

| Pregunta | Por qué no basta el dashboard | Herramientas |
|---|---|---|
| «¿Por qué el modelo ve valor en el Betis–Sevilla, más de 2,5?» | El dashboard enseña P, EV y unas líneas de resumen; el chat puede juntar λ de local y visitante, forma, bajas y medias de la previa y explicarlo con palabras | `lineas_de_partido`, `previa_partido` |
| «¿Qué combinada de hoy encaja con mi perfil (doble oportunidad, nada de menos goles)?» | Los picks del día son una sola combinada fija; aquí se pueden pedir alternativas con otras restricciones | `cuotas_con_valor`, `picks_de_hoy` (fase 2: `combinadas_perfil`) |
| «¿Cómo va el v2 en dobles oportunidades este mes?» | Transparencia agrega por versión del modelo, pero **no** por mercado ni por fechas | `metricas_picks` (necesita un filtro nuevo, ver §3) |
| «¿Qué apuestas mías siguen pendientes y cuánto llevo de ROI?» | Ya está en Mis apuestas; el chat solo lo resume | `mis_apuestas` |
| «¿Están frescos los datos? ¿Cuándo fue el último refresco?» | Está repartido entre la portada y los logs | `estado_datos` |

### Dónde no aporta (o empeora)

- Ver la lista de cuotas, ordenarla y filtrarla: la tabla del dashboard es más rápida y exacta.
- Cualquier cálculo de P o EV: lo hace el modelo Dixon–Coles. El LLM **solo cita**, nunca calcula.
- Lanzar scrapers o refrescos desde el chat: hay riesgo de bloqueo por IP y no se gana nada.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| **Que invente probabilidades, cuotas o EV** | Las herramientas devuelven números ya formateados y con nombre (`p_modelo`, `cuota`, `ev`), junto con `datos_de` y `generado_en`. Las instrucciones del servidor MCP (o el *system* en B) dicen: «cita solo números que hayan salido de una herramienta en esta conversación; si no hay herramienta para algo, dilo; no calcules P ni EV». En B se puede comprobar a posteriori: cada cifra con % o cuota del texto debe aparecer en algún `tool_result`, y si no, la UI muestra un aviso. |
| **Tono de consejo de apuestas** | Es un proyecto educativo. Las instrucciones piden lenguaje de análisis («el modelo estima…»), nunca «apuesta a…», y recordar que el modelo no está calibrado del todo. Cada respuesta con picks lleva una línea fija de juego responsable (+18, jugarbien.es, 900 200 225 *(teléfono sin verificar)*). En B, el aviso va fijo en el panel y no depende del modelo. |
| **Inyección de instrucciones a través de datos scrapeados** (nombres de equipo, jugador y liga, y motivos de baja, vienen de Winamax/Flashscore) | 1) En la v1 todas las herramientas son de solo lectura: lo peor que puede conseguir un texto inyectado es engañar en la respuesta, no ejecutar acciones. 2) La capa recorta cada cadena externa (≤ 80 caracteres, sin saltos de línea ni caracteres de control) y la devuelve en campos JSON, nunca mezclada con instrucciones. 3) Las instrucciones dejan claro que lo que devuelven las herramientas son datos, no órdenes. 4) Si algún día hay escritura, con confirmación explícita (ver §3). |
| **Exposición si el servidor escucha en red** | stdio no abre puertos. Si alguna vez se pasa a HTTP: solo en `127.0.0.1` (por defecto el SDK solo acepta peticiones dirigidas a localhost y responde 421 a las demás), con token *bearer* y nunca con un túnel público, porque hay datos personales (Mis apuestas). |
| **Efectos secundarios ocultos en servicios «de lectura»** | `MatchPreviewService.build` puede **ir a Flashscore** a por la alineación oficial (a menos de 75 min del partido) y hace `commit` (`match_lineups.py`, `_official`). La herramienta le pasa un `MatchLineupService(fetch_players=...)` que no sale a la red. **No** se usan `asegurar_picks_de_hoy` ni `POST /refresh/*`, porque generan y congelan picks o lanzan scrapers. |
| **stdout contaminado rompe stdio** | En un servidor stdio, cualquier `print` o log que vaya a stdout corrompe el protocolo. Los logs van solo a stderr o a `logs/`, y no se llama a `force_utf8_stdio()`, que reasigna `sys.stdout` *(compatibilidad con el SDK sin verificar)*. |
| **Datos personales** | `mis_apuestas` pasa importes y resultados al cliente de Claude. Con stdio y tu cuenta, es lo mismo que pegarlos en el chat. Se puede dejar desactivada con una variable. |

---

## 3. Catálogo inicial de herramientas (v1: solo lectura)

Convenciones comunes a todas:
- Anotaciones MCP `readOnlyHint=true` y `openWorldHint=false`.
- Respuesta JSON compacta, con claves cortas en español.
- Fechas en hora de Madrid (`"sáb 15/11 21:00"`).
- Cuotas y probabilidades como texto ya formateado (`"1,85"`, `"54,1 %"`, `"+4,2 %"`), con los mismos
  criterios que `core/format.ts`, para que el LLM no redondee por su cuenta.
- Siempre con tope de filas (`limite` ≤ 25–50).
- Cada respuesta lleva `datos_de` (última toma de cuotas o de stats) y, si se ha recortado, `truncado: true`.

| # | Herramienta | Parámetros | Reutiliza | Respuesta (forma) |
|---|---|---|---|---|
| 1 | `estado_datos`: frescura de los datos y modelo activo | — | `refresco_auto.historial_de_refresh`, `model_registry.version_activa` | `{ultimo_refresh, estado, cuotas_de, modelo:{version, desde}}` |
| 2 | `buscar_partidos`: partidos por equipo, liga o fecha | `texto?`, `liga?`, `dias=3` (≤14), `dias_atras=0` (≤7), `limite=20` | La consulta de `GET /matches` (hoy está dentro del router: **hay que moverla a `services/match_listing.py`**) + `latest_1x2_by_match` | `[{id, fecha, liga, local, visitante, estado, marcador?, 1x2:[c1,cX,c2]?}]` |
| 3 | `cuotas_con_valor`: mejores líneas por EV | `horas=48`, `liga?`, `mercado?` (grupo: `1x2`, `doble`, `goles`, `ambos`, `jugador`…), `ev_min=0`, `solo_recomendables=true`, `incluir_menos_goles=false`, `limite=20` | `graded_odds_service.list_graded_winamax_odds` + `daily_picks_v2.grupo_de_mercado` / `es_menos_goles` | `[{match_id, partido, fecha, mercado, seleccion, cuota, p_modelo, p_implicita, ev, confianza}]` |
| 4 | `lineas_de_partido`: todas las líneas de un partido con su explicación | `match_id`, `mercado?`, `limite=30` | `list_graded_winamax_odds(match_id=...)` | `{partido, lambda:{local, visitante}, version_modelo, lineas:[{…como en la 3…, resumen:[≤3 frases de model_summary_lines]}]}` |
| 5 | `previa_partido`: medias, forma, bajas, once y jugadores clave | `match_id` | `match_preview.MatchPreviewService` **sin red** (ver riesgos) | `{local:{medias, mitades, forma:"VVEDV", bajas:[…], once?:"probable/oficial", clave:[{nombre, tiros_p90…}]}, visitante:{…}}` |
| 6 | `picks_de_hoy`: picks congelados de hoy | — | `featured_daily_picks.consultar_picks_de_hoy` (**no** `asegurar_…`) | `{estado:"listo/pendiente/sin_partidos", simples:[…], combinada:{patas, cuota, p, ev, sin_valor}}` |
| 7 | `historial_picks`: picks de días anteriores con su resultado | `dias=14` (≤60) | La consulta de `GET /featured-picks/history` + `simples_corregidas` / `combinada_corregida` | `[{fecha, simples:[{seleccion, cuota, resultado}], combinada:{cuota, resultado}}]` |
| 8 | `metricas_picks`: acierto por versión, mercado y periodo | `desde?`, `hasta?`, `version?`, `mercado?` | `aggregate_leg_metrics` + **filtro nuevo** por fechas y `grupo_de_mercado` (función pura sobre `simples_corregidas`, con test) | `{periodo, por_version:{v2-…:{simples:{aciertos, fallos, pct}, combinadas:{…}}}}` |
| 9 | `mis_apuestas`: apuestas guardadas y resumen | `estado?` (`pendiente/acertada/fallada`), `limite=20` | `saved_parlays.resumen_apuestas` + la consulta de `GET /me/parlays` (usuario: el único `AppUser`, o uno fijado por configuración) | `{resumen:{total, pct, apostado, beneficio, roi}, apuestas:[{fecha, titulo, cuota, importe, resultado, patas:[…]}]}` |
| 10 | `stats_equipo`: temporada de un equipo | `equipo` (nombre o id) | `TeamSeasonStats` + `MatchPreviewService._team` (habría que exponer una función pública) | `{equipo, liga, pj, gf, gc, medias:{…}, forma}` |

Para la fase 2, si las de la v1 se usan:
- `combinadas_perfil(n=3, patas=3..5, cuota_total=4..6)`, que reutiliza `candidatas_desde_lineas`,
  `enumerar_combinadas` y `afinidad_con_perfil` de `daily_picks_v2` (es pura y no guarda nada).
- Un *prompt* MCP `analista` con las reglas de §2 para arrancar las conversaciones.

**Sobre la regla «la API solo expone lo que usa el frontend»**: la opción A no añade endpoints. El
servidor MCP es una entrada nueva, como los scripts de `tools/`, y quien lo llama es Claude Code o
Desktop. Lo único nuevo de verdad son dos piezas pequeñas:
- Mover la consulta de `GET /matches` a un servicio. La llamarían el router y la herramienta, y de paso
  se arregla un router que hoy no se limita a delegar.
- El filtro por fechas y mercado de las métricas. Solo lo llama la herramienta 8: si no vas a hacer esas
  preguntas, la herramienta 8 se queda sin filtro y no se añade nada.

### Acciones de escritura (fuera de la v1)

| Acción | ¿Tiene sentido? | Cómo, si se hace |
|---|---|---|
| Guardar una apuesta en «Mis apuestas» | **Sí, en una fase posterior (M5).** Encaja con «¿qué combinada me encaja?» → «guárdala con 3 €» | Una herramienta `guardar_apuesta` que reutiliza `snapshots_from_payload` + `combine_products` y **solo acepta líneas que existan en BD** (por id de cuota), nunca cuotas escritas por el modelo. Con `destructiveHint=false` y `_meta["anthropic/requiresUserInteraction"]=true`, para que Claude Code pida confirmación en cada llamada sin que una regla *allow* se la salte. En B, la confirmación es un botón «Confirmar» del panel, no el modelo. |
| Lanzar «Actualizar cuotas» | **No.** Es un scraper con límite de 1 cada 3 min y riesgo de bloqueo, y el botón del dashboard ya existe | — |
| Generar picks o recalificar | **No.** Los picks se congelan y hay un trigger en BD; no debe dispararlo un LLM | — |

---

## 4. Recomendación y plan

**Opción C, empezando por A (MCP local por stdio, solo lectura, desde Claude Code).** Por qué:
1. Es lo único que da el chat **hoy, gratis y sin abrir nada a la red**.
2. Reutiliza los `services/` tal cual, y la capa `backend/chat/` sirve después para B sin reescribir nada.
3. El riesgo grande (que el LLM invente números) se ataca igual en A que en B, y en A se prueba sin pagar.
4. B solo merece la pena si, tras usar A unas semanas, el chat se usa de verdad y quieres enseñarlo
   dentro de la app.

### Bloques

| Bloque | Qué | Hecho cuando |
|---|---|---|
| **M0** (una tarde) | Añadir `mcp` a `requirements.txt`; crear `backend/chat/herramientas.py` con `estado_datos`, `cuotas_con_valor` y `picks_de_hoy`; crear `backend/tools/servidor_mcp.py` (`MCPServer` por stdio, anotaciones de solo lectura, logs a stderr); añadir `.mcp.json` en la raíz | En Claude Code, `/mcp` lista el servidor y la pregunta «¿qué líneas con valor hay para mañana?» se responde con cuotas que coinciden con las del dashboard |
| **M1** | Formateadores compactos y saneado de las cadenas externas como funciones puras, con tests (`TestHerramientasChat`, sin BD); instrucciones del servidor (citar solo datos de las herramientas, tono, juego responsable) | `pytest` y `ruff` en verde; una respuesta con picks incluye el aviso; un nombre de equipo con «ignora las instrucciones…» se devuelve recortado y como dato |
| **M2** | `lineas_de_partido`, `previa_partido` (sin red) y `buscar_partidos` (moviendo la consulta de `GET /matches` a `match_listing.py` sin cambiar el endpoint) | «¿Por qué el modelo ve valor en X?» se explica con λ, forma y bajas, y al pedir la previa no aparece ningún log de Flashscore |
| **M3** | `historial_picks`, `metricas_picks` (con el filtro por fechas y mercado y su test), `mis_apuestas` y `stats_equipo` | «¿Cómo va el v2 en dobles oportunidades este mes?» da el mismo % que sale a mano en Transparencia |
| **M4** | Documentar en el README la configuración de Claude Desktop (ruta al `python.exe` del `.venv` y `PYTHONPATH` a la raíz) y añadir una nota en HOJA_DE_RUTA | Funciona igual desde Claude Desktop |
| M5 *(opcional)* | `guardar_apuesta` con confirmación obligatoria | Guardar desde el chat pide confirmación y la apuesta aparece en Mis apuestas con la cuota de la BD |
| B1–B3 *(opcional)* | `POST /chat` por SSE con el tool runner sobre `backend/chat/`, panel Angular (tokens de marca, `signal`), aviso fijo, tope de coste por día y comprobación de las cifras citadas | Hay conversación en la app con streaming, y el coste por conversación que queda en el log coincide con la estimación |

Esto es trabajo nuevo, no de la fase 3 (el modelo). Conviene apuntarlo en HOJA_DE_RUTA como un bloque
aparte, para no mezclarlo con B0–B13.

---

## 5. Decisiones que tienes que tomar

| # | Decisión | Opciones | Recomendación |
|---|---|---|---|
| 1 | ¿Desde dónde quieres chatear? | a) Claude Code · b) Claude Desktop · c) claude.ai web · d) dentro de la app | **a y b** (stdio, gratis). c obliga a publicar el servidor; d cuesta por uso |
| 2 | ¿Se hace el chat en la app (B)? | a) No · b) Más adelante, después de usar A · c) Ya | **b** |
| 3 | Si hay B, ¿qué modelo? | Haiku 5.5 (~0,6 $/mes) · Sonnet 5.5 (~10 $/mes) · Opus 5.5 (~20 $/mes) | Probar con **Haiku 5.5** y subir a Sonnet 5.5 si explica mal. La decisión es tuya: la referencia de la API recomienda Opus 5.5 por defecto |
| 4 | ¿Exponer «Mis apuestas» al chat? | a) Sí · b) No · c) Sí, pero desactivable con una variable | **c** |
| 5 | ¿Permitir guardar apuestas desde el chat? | a) Nunca · b) En la fase M5, con confirmación | **b**, cuando la parte de solo lectura lleve un tiempo funcionando |
| 6 | ¿Herramienta de métricas por mercado y fechas (lógica nueva)? | a) Sí · b) No, bastan las métricas actuales | **a** si de verdad vas a preguntar «¿cómo va en dobles oportunidades?»; si no, **b** |
| 7 | ¿`.mcp.json` en el repo (compartido) o `claude mcp add` (solo en tu máquina)? | a) En el repo · b) Local | **a**: no lleva secretos (el `.env` se lee aparte) y queda documentado para el portfolio |

---

## Fuentes

- Referencia de la API de Claude (skill `claude-api`, caché del 2026-11-03): modelos y precios, Tool
  Runner, prompt caching, conector MCP y disponibilidad por plataforma.
  Precios: <https://platform.claude.com/docs/en/about-claude/pricing> ·
  caché: <https://platform.claude.com/docs/en/build-with-claude/prompt-caching>
- Conector MCP de la API (exige un servidor público por HTTP, solo *tools*, beta `mcp-client-2025-11-20`):
  <https://platform.claude.com/docs/en/agents-and-tools/mcp-connector>
- MCP en Claude Code (`claude mcp add`, ámbitos, `.mcp.json`, `anthropic/requiresUserInteraction`):
  <https://code.claude.com/docs/en/mcp>
- Claude Desktop con servidores locales (`claude_desktop_config.json`):
  <https://modelcontextprotocol.io/docs/develop/connect-local-servers>
- SDK de MCP para Python (vía Context7, `/modelcontextprotocol/python-sdk`): `MCPServer`, `mcp.run()` por
  stdio, `ToolAnnotations(read_only_hint=...)`, lifespan, montaje ASGI y restricción a localhost:
  <https://github.com/modelcontextprotocol/python-sdk/tree/main/docs>
- Versiones publicadas en PyPI, consultadas el 2026-11-13: `mcp` 2.3.0 y `anthropic` 2.8.0.
