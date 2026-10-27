# Plan de la fase 3 (modelo v2, picks del día y aprendizaje)

Plan del agente arquitecto del **2026-10-21**, con las correcciones del revisor, aprobado por el
usuario ("empieza por B0–B2"). Hasta el 2026-10-25 solo estaba en el historial de aquella sesión;
se guarda aquí para no perderlo. El estado de cada bloque se lleva en
[HOJA_DE_RUTA.md](HOJA_DE_RUTA.md); la investigación de modelos externos, en
[INVESTIGACION_MODELOS.md](INVESTIGACION_MODELOS.md).

## Decisiones del usuario (2026-10-21)

- **Modelo base:** estadístico mejorado (no ML puro). **Solo esta temporada**, sin temporadas anteriores.
- **Mercados que le van bien:** doble oportunidad, tiros a puerta y goles en la 2ª mitad (prepartido;
  en Winamax es "Mitad con más goles", betType 3317). Tiros a puerta **también de jugador**.
- **Picks del día:** al menos **3 simples de cuota 1,30–1,70** con la mejor probabilidad y **una
  combinada de cuota total 4–6**. Si no hay 3 con valor (EV ≥ 0), **se completa hasta 3 avisándolo**.
- **Día de los picks:** hora de Madrid. Se generan al abrir la app o en el refresh programado, lo que
  llegue antes (decidido el 2026-10-25; si las cuotas tienen más de 2 h, se actualizan antes).
- **Importes:** el modelo **no sugiere cuánto apostar** (router `profiles` y Kelly retirados).
- **Aprender de Mis apuestas:** prioriza los mercados del usuario (no cambia las probabilidades). No
  cuentan las apuestas guardadas con el partido ya empezado. La combinada personal usa la cuota
  habitual del usuario (~3).
- **Eliminatorias con prórroga** (UEFA desde febrero, copas): excluidas de momento.
- **Forma de trabajar:** con aprobación del usuario en cada paso.
- **2026-10-25:** se adelanta **B10 (picks v2) con el modelo actual** (`legacy-v1`), para medir el
  acierto desde ya; el v2 lo sustituirá cuando pase el backtest. Cambio respecto al plan: las patas
  candidatas de la combinada son la mejor línea de cada **tramo de cuota** por partido (1,25–1,45,
  1,45–1,70 y 1,70–2,00), no "las 3 de mayor P" (con esas nunca se llegaba a 4).
- **2026-10-25 (más tarde):** la combinada pasa a **cuota total 3–6** (antes 4–6). Si con partidos
  distintos no se llega, se admiten **varias selecciones de un mismo partido** (MyMatch en
  Winamax): P conjunta con la matriz de marcadores del modelo y cuota estimada con esa
  correlación, más la cuota mínima con valor (1/P). **La combinada nunca cambia las simples.**
  Regla de elección (`puntuar_combinada`): con valor → partidos distintos → mayor P conjunta →
  menos patas (no el mayor EV: con un modelo sin calibrar, eso elige sus mayores errores).
  Donde abajo pone "4–6", léase 3–6.
- **2026-10-25 (noche):** B3, B4, B5 (parcial), B7, B8 y B9 hechos; versión v2 activa. Cambios
  respecto al plan: ρ se estima aparte con las λ fijas; la mezcla con el mercado (prevista para
  v3) entra ya, con el peso elegido por el backtest (60 %), porque el cierre de Winamax ganaba al
  modelo solo; no hay tabla `model_fits` ni `prediction_snapshots` (los ratings se reajustan al
  momento y los picks guardan la etiqueta de la versión).

---

## Plan del arquitecto

Exploración: graphify (query/explain) y lecturas puntuales. No he modificado ningún archivo. Las referencias van como `archivo:línea`.

---

## 0. Antes de empezar: cuatro bloqueos

1. **La liquidación no cubre los mercados del usuario.** `grade_single_leg` devuelve `needs_review` para:
   - doble oportunidad (`backend/services/saved_parlay_grading.py:67-68`);
   - tiros a puerta y cualquier `match_*_totals` con corners/cards/shots/sot (`:70-73`);
   - cualquier mercado que no sea `totals` o goles por equipo, lo que incluye las mitades (`:102-103`).

   Consecuencia: ni los picks de doble oportunidad ni Mis apuestas se pueden liquidar solos, y el perfil del usuario no tendría de qué aprender. **Es el primer bloque.**
2. **Hoy no se capturan los tiros a puerta por equipo.** En la muestra `data/raw/winamax_muestras/match_72221330_state.json` están los betType 6206 ("Número de tiros a puerta por Tottenham", el local) y 6207 (el visitante), pero `MARKET_TYPES` (`backend/scrapers/winamax.py:82-132`) solo mapea 6205 (`match_sot_totals`).
3. **Hoy no se capturan los goles de la 2ª parte.** Las claves `match_second_half_totals` y `team_*_second_half_totals_goals` aparecen en `winamax_normalization.py:33-37` y `winamax_odds_ingester.py:120-124`, pero no tienen betType en `MARKET_TYPES`. En la muestra no hay ningún "2ª mitad – Nº total de goles" antes del partido (solo goleadores 5658 y 5660, y "Mitad con más goles" 3317). Sin cuota no hay pick ni CLV (ver pregunta 1).
4. **El picker actual tiene fallos:**
   - `MAX_FEATURED_OFFERED_ODDS = 2` (`featured_daily_picks.py:19`) se declara pero no se usa en `select_top_distinct_matches` (`:48-73`).
   - El horizonte es de 14 días (`:128`), así que no son "picks del día".
   - `POST /featured-picks/ensure-date` acepta fechas pasadas (`api/featured_daily_picks.py:86-96`) y genera filas "de ayer" con datos de hoy.
   - `GET /today` genera las filas de forma perezosa (`:37-47`), aunque las cuotas sean viejas.
   - En modo no oficial, `_build_value_bets` se salta la casa más afilada (`value_detector.py:1103`). Con Winamax como única casa no saldría nada, así que en el `.env` debe estar `official_data_pipeline=True`. Hay que confirmarlo en el bloque B0 sin abrir el `.env`, por ejemplo con `get_settings()`.

---

## 1. Diagnóstico del modelo actual

**Qué hace bien**
- `poisson_model.py` es puro y está testeado: matriz 8×8 y Dixon–Coles con ρ (`:183-228`).
- Saca 1X2, O/U 1,5/2,5/3,5, BTTS y doble oportunidad (DC) como propiedades (`:94-108`).
- Tiene líneas arbitrarias con push (`probability_over_total` `:247-313`) y Poisson simple para totales por equipo (`:316-353`).
- Hay walk-forward sin fuga (`poisson_backtest.py:100-218`) y métricas de log-loss, Brier y fiabilidad (`poisson_calibration.py:137-277`).
- `graded_odds_service.list_graded_winamax_odds` (`:619-793`) ya calcula P y EV de cada línea de Winamax con `Decimal`, y aplica `MAX_RECOMMENDABLE_ODDS` (`grade_line` `:550-559`).

**Qué le falta**
- **Fortalezas = ratios de la temporada sin encoger.** `_to_averages` (`value_detector.py:1205-1214`) y `expected_goals` (`poisson_model.py:147-173`) dividen GF/GA entre la media de la liga. Con 8–10 partidos por equipo eso es muy ruidoso: no hay regresión a la media ni estimación conjunta (no se ajusta por la fuerza del rival).
- **Ventaja de campo fija en 1,10** (`:38`), igual para todas las ligas.
- **La "forma" es una mezcla ad hoc:**
  - `_blend_season_with_venue_rates` (`value_detector.py:209-225`) con peso 0,38 (`config.py:67-74`).
  - `recent_goal_form.recent_rates_as_home` (`:20-50`) no tiene corte temporal (solo `exclude_match_id`). En producción vale, pero en backtest o al regenerar habría fuga.
- **Fuente con fuga para el backtest:** `_ensure_league_cached` (`value_detector.py:381-415`) lee `TeamSeasonStats`, que es la clasificación "de ahora". El backtest de producción no lo puede reutilizar.
- **UEFA:** las fortalezas salen solo de los ~3 partidos UEFA de la liga (`TeamSeasonStats` por `season_id` de esa liga) y no del historial doméstico del equipo.
- **Inconsistencia:** los totales se calculan sin ρ (`value_detector.py:768`, falta `dixon_coles_rho`), aunque el 1X2 sí lo usa (`:362-365`).
- **ρ = 0 en la práctica:** se lee del último `CalibrationReport` (`:367-379`) y hay 0 informes.
- **No hay modelo** para tiros a puerta por equipo o partido (`match_sot_totals` se guarda pero `_model_probability` `:689-794` no lo trata), ni para mitades (`match_first/second_half_totals`). Solo hay textos en `graded_odds_service.py:277-312`.
- **No se usa:** xG (`match_stats.xg`, `models/match_stats.py:60`) ni bajas (`match_absences` solo llega a la previa y las alineaciones).
- **El backtest no sirve hoy:** `tools/backtest_poisson.py:1-24,40` descarga la temporada anterior de Sofascore, que responde 403 y además es una temporada que el usuario no quiere usar.
- **Sin trazabilidad:** `FeaturedDailyPick` (`models/featured_daily_pick.py`) no tiene versión del modelo, hora de generación, hash ni protección contra cambios en `picks_json`.
- **Nota de convenciones:** el modelo calcula en `float`. Se acepta en el núcleo numérico (numpy y scipy ya están en `requirements.txt`) siempre que todo lo que sale a servicios, BD o API pase a `Decimal` cuantizado (6 decimales para P).

---

## 2. Qué mejoras entran, en qué orden y por qué

Datos disponibles: 400 partidos terminados (≈ 8–10 por equipo en liga), `match_stats` en 355 (710 filas), `odds_history` en 191 partidos.

| # | Mejora | ¿Entra? | Justificación |
|---|---|---|---|
| 1 | **Dixon–Coles por máxima verosimilitud penalizada (ridge)**: log λ = μ_liga + hfa_liga + att_i − def_j, con previo N(0, σ²) en att/def (eso es la regresión a la media) y ρ estimado | **Sí, v2 núcleo** | Con 8–10 partidos, el encogimiento es lo que más mejora. Ajusta por la fuerza del rival. Usa scipy L-BFGS. |
| 2 | **Decaimiento temporal** con peso w = exp(−ξ·días) dentro de la verosimilitud | **Sí**, como hiperparámetro ξ con semividas ∞ / 90 / 45 días | Sustituye a la mezcla de "forma" ad hoc. La temporada solo tiene 67 días, así que es probable que gane ∞; lo decide el backtest. |
| 3 | **Ventaja de campo por liga**, encogida hacia la global | **Sí** | Hay de 54 a 89 partidos por liga, suficiente con encogimiento. **No** se estima ventaja por equipo (≈ 4–5 partidos en casa por equipo es puro ruido). |
| 4 | **Equipos compartidos entre liga y UEFA**, con desplazamiento por liga μ_liga y previo N(0; 0,15²) | **Sí** | Los partidos UEFA conectan las ligas; sin previo el desplazamiento entre ligas está mal identificado. |
| 5 | **Mezcla con xG**: segundo ajuste ridge sobre xG (quasi-Poisson) y log λ = w·goles + (1−w)·xG | **Sí, condicionada** a cobertura de xG ≥ 80 % (se mide en B0) | El xG reduce mucho el ruido con pocos partidos; w ∈ {1; 0,7; 0,5}. |
| 6 | **Mitades**: λ_2ª = λ·s y λ_1ª = λ·(1−s), con s (cuota de goles de la 2ª parte) por liga encogida a la global (~0,55), sacada de `goals_1h/goals_2h` | **Sí** | Un parámetro por liga, robusto. Por equipo se descarta (ruido). Sirve para el modelo aunque falte la cuota de 2ª parte (bloqueo 3). |
| 7 | **Tiros a puerta por equipo**: GLM binomial negativa ridge (attSoT_i, defSoT_j, campo) con dispersión global; tiros a puerta del partido por convolución | **Sí** | Hay `shots_on_target` por equipo y partido; la binomial negativa absorbe la sobredispersión. |
| 8 | **Capa de calibración por familia**: p_cal = σ(a + b·logit p), con b ∈ [0,5; 1] (solo puede bajar la confianza), ajustada walk-forward y solo si n ≥ 150; si no, identidad | **Sí** | Es la protección principal contra el EV inflado. |
| 9 | Bajas | **No en v2** | No hay modelo fiable del impacto de cada jugador con 10 partidos, y `captured_at` de las bajas puede ser posterior al saque. Siguen solo como información en la previa. |
| 10 | Mezclar con la probabilidad sin margen del mercado | Como **versión candidata v3** | Solo se puede evaluar en los 191 partidos con historial. Si mejora el log-loss, se promueve por la regla normal. |

---

## 3. Arquitectura propuesta

### 3.1 Servicios (`backend/services/`). Nuevos son puros salvo los cargadores de BD.

- **`model_dataset.py` (nuevo).**
  - `TrainingMatch` (dataclass congelada): liga, ids de equipos, saque, goles, goles por mitad, tiros a puerta, xG.
  - `load_training_matches(session, cutoff)`: carga de BD solo de partidos con `status=finished` y `commence_time < cutoff`.
  - `assert_sin_fuga(matches, cutoff)`: función pura que verifica el corte.
- **`team_ratings_model.py` (nuevo).**
  - `RatingsHyper` (σ, ξ, peso de xG, previo de liga).
  - `fit_ratings(matches, cutoff, hyper) -> FittedRatings` (att/def por equipo, hfa por liga, μ por liga, ρ y n por equipo).
  - `predict_goal_lambdas(fit, league_id, home_id, away_id)`.
- **`sot_model.py` (nuevo).** `fit_sot(...)`, `predict_sot_means(...)`, función de probabilidad de la binomial negativa.
- **`market_probabilities.py` (nuevo).** `MatchPrediction` (λ, ρ, s, medias de tiros a puerta, dispersión) y `probability_for_line(pred, market_key, outcome_name, point, home_name, away_name) -> Decimal | None`. Cubre:
  - h2h, double_chance (reutiliza `normalize_double_chance_outcome`, `winamax_normalization.py:126`);
  - totals con ρ (corrige el fallo de `:768`), totales por equipo, btts;
  - 1ª y 2ª mitad (partido y equipo);
  - `match_sot_totals` y `team_home/away_sot_totals`.

  Reutiliza `_build_grid` y `probability_over_single_poisson` de `poisson_model.py`.
- **`model_calibration_v2.py` (nuevo).** Familia de mercado (`market_family(market_key)`), ajuste Platt acotado y métricas por familia: log-loss, Brier y ECE con 5 bins.
- **`model_backtest.py` (nuevo).**
  - Walk-forward por día.
  - Bases de comparación:
    - el modelo actual reconstruido con `poisson_backtest`;
    - la tasa base;
    - la probabilidad sin margen de cierre, sacada de `odds_history`.
  - Bootstrap pareado.
  - `closing_price(history_rows, kickoff)` y CLV.
- **`model_registry.py` (nuevo).**
  - `should_promote(active, candidate) -> (bool, motivos)` (pura).
  - `get_active_fit(session)`.
  - `refit_active(session, now)` para el reajuste nocturno con los mismos hiperparámetros.
- **`daily_picks_v2.py` (nuevo, puro).** `select_singles(...)` y `select_combo(...)` (algoritmo en la sección 4).
- **`user_market_profile.py` (nuevo, puro + cargador).** `build_profile(legs_liquidadas, now) -> dict[familia, PerfilFamilia]` y `rerank_for_user(candidatos, perfil)`.
- **Modificados:**
  - `saved_parlay_grading.py`: nuevo parámetro opcional `stats: tuple[MatchStats|None, MatchStats|None] | None`. Liquida doble oportunidad con el marcador y mitades y tiros a puerta con `match_stats`. Si el partido ha terminado pero no hay stats, devuelve el nuevo estado `pending_stats`, que pasa a `needs_review` a las 72 h. Se ajusta `overall_from_leg_results` (`:127-134`).
  - `saved_parlays.refresh_one_parlay` (`:49-67`) y `featured_daily_picks._refresh_one_row` (`:76-96`): cargan `MatchStats` de los partidos.
  - `graded_odds_service.list_graded_winamax_odds`: si `model_v2_enabled` y hay un ajuste activo, la P sale de `market_probabilities` y no de `det._model_probability` (`:738-747`). Si no, sigue como hoy.
  - `featured_daily_picks.py`: `generate_picks_for_date` pasa a usar las líneas del graded odds v2 y `daily_picks_v2`. `ensure_row_for_date` solo permite hoy.
  - `daily_refresh.py` (`:294-301`):
    - nuevo paso "reajuste del modelo" (solo en la primera ejecución del día);
    - "generar picks de hoy si faltan" después de la ingesta de cuotas;
    - luego "recalificar".
  - `winamax.py:82-132`: 6206 → `team_home_sot_totals`, 6207 → `team_away_sot_totals`. Las dos claves se añaden a `TOTALS_MARKETS` (`winamax_normalization.py:20-38`) y a las etiquetas del ingester (`:117-126`).
  - `config.py`: `model_v2_enabled`, `picks_odds_min=1.30`, `picks_odds_max=1.70`, `combo_odds_min=4`, `combo_odds_max=6`, `picks_min_kickoff_margin_min=30`, `picks_max_odds_age_h=6`, `profile_min_legs=15`, `profile_prior_k=20`, `profile_half_life_days=45`.

### 3.2 Modelos SQLAlchemy y migraciones

Cada uno en su archivo, exportado en `models/__init__.py`. Una migración por bloque, revisada a mano; la cabeza actual es `333abb2b3059`.

| Tabla | Columnas clave | Alcance |
|---|---|---|
| `model_versions` | id, `tag` único (p. ej. `dc-ridge-v2.0`), `algorithm`, `hyperparams` JSON, `enabled_families` JSON, `status` (candidate/active/retired), `backtest_metrics` JSON, `promoted_at`, `retired_at`, `notes` | Global |
| `model_fits` | id, `model_version_id` FK, `trained_until` timestamptz, `n_matches`, `params` JSON, `params_sha256`, `created_at` | Global |
| `prediction_snapshots` | BigInteger id, `match_id`, `model_fit_id`, `market_key`, `outcome_name`, `outcome_point`, `player_id` null, `p_raw`, `p_model` Numeric(8,6), `offered_odds` Numeric(8,3), `odds_captured_at`, `created_at`; índice (match_id, market_key) | Global. Registro del forward-test real: cada línea candidata al generar los picks (≈ 3k filas/día) |
| `featured_daily_picks` (alterar) | `rules_version` (v1 en las filas existentes), `generated_at`, `model_fit_id` FK null, `combo_json` JSON null, `selection_meta` JSON (motivos, recuentos), `picks_sha256`. **Trigger PL/pgSQL** que rechaza el UPDATE de `picks_json`, `combo_json`, `picks_sha256` y `model_fit_id` (el downgrade lo elimina) | Global |
| `user_daily_picks` | id, `user_id` FK, `pick_date`, `featured_pick_id` FK, `legs_json`, `profile_snapshot` JSON, `created_at`; único (user_id, pick_date); mismo trigger de inmutabilidad | Por usuario |

El perfil de mercados **no** lleva tabla: se calcula al vuelo (hay pocas apuestas) y se congela en `user_daily_picks.profile_snapshot`. No se reutiliza `user_profiles` (`models/user_profile.py`): es heredada, va con Kelly y stake y no está ligada a `app_users`.

### 3.3 Endpoints

Los routers solo delegan. Se registran en `_ROUTERS_PROTEGIDOS` (`app/main.py:57-67`).

- `GET /featured-picks/today`: ya no genera de forma perezosa. Devuelve la fila o `{estado: "pendiente"}`. Respuesta ampliada:
  - `legs` (simples, se mantiene por compatibilidad);
  - `combo` (`{legs, combined_odds, joint_probability, ev, sin_valor}` o null);
  - `model_version`, `generated_at`, `selection_meta`.
- `GET /featured-picks/history`: igual, ampliada. Las filas v1 salen con `rules_version: "v1"`.
- `GET /featured-picks/metrics`: añade desglose simples/combinada, con valor/sin valor y CLV medio cuando haya datos.
- `POST /featured-picks/ensure-date`: solo hoy; con otra fecha devuelve 422.
- `GET /model/active`: etiqueta, fecha del ajuste, familias habilitadas y métricas del backtest (para Transparencia).
- `GET /me/market-profile`: perfil por familia (n, aciertos, aciertos esperados, ratio encogido, uso, estado "aprendiendo n/15").
- `GET /me/picks/today`: picks personales; se congelan la primera vez que se piden y solo con partidos que aún no han empezado.
- **Ningún endpoint nuevo devuelve importes.** `vc.recommended_stake` y `kelly_fraction` (`value_calculator.py:114,133`) no se usan en esta fase.

### 3.4 Frontend (cambios mínimos, tokens `tv-*` de DESIGN.md)

- **Transparencia** (`pages/transparencia/*`, `core/models.ts:247-262`, `api.service.ts:277-293`):
  - bloque "Simples del día" con la insignia **"Sin valor"** (EV < 0);
  - bloque "Combinada del día" (cuota total y P conjunta);
  - pie "Modelo `<tag>` · congelado a las HH:MM";
  - estado "pendiente de generar";
  - las filas v1 se siguen pintando como antes;
  - métricas separadas con valor/sin valor.
- **Mis apuestas**: panel "Tus mercados" (familias, n, rendimiento frente a lo esperado, "aún aprendiendo") y bloque "Para ti" con los picks personales, sin importes.

---

## 4. Algoritmo de picks del día (global, congelado)

**Cuándo se genera.** Una vez al día, en el primer refresh tras la ingesta de cuotas de Winamax (09:00), con el ajuste activo. Si no hay ajuste activo, se usa el modelo actual y se marca `model_version="legacy-v1"`.

**Candidatos.** Líneas de Winamax que cumplen todo esto:
1. Partido con saque en [ahora + 30 min, fin del día civil].
2. Snapshot de cuotas de hace ≤ 6 h.
3. Familia dentro de `enabled_families` de la versión activa (solo las que pasaron la calibración, ver sección 6). Al principio, **sin props de jugador**.
4. Los dos equipos con ≥ 5 partidos de la temporada en los datos (para mostrar solo en pantalla basta con ≥ 3).
5. P(modelo) calibrada disponible.

Por cada candidato se registra una fila en `prediction_snapshots`.

**Simples:**
1. Banda: 1,30 ≤ cuota ≤ 1,70.
2. Una línea por partido: la de mayor p_cal. Si hay líneas redundantes del mismo partido, como "Más de 0,5" y "Más de 1,5", gana la de mayor p dentro de la banda.
3. EV = cuota × p_cal − 1, en `Decimal`.
4. Orden: p_cal descendente, luego EV descendente, luego `match_id` ascendente (determinista).
5. Nivel A = EV ≥ 0. Se toman hasta `MAX_SIMPLES = 5` del nivel A.
6. Si quedan menos de 3, se completa hasta 3 con el nivel B (EV < 0) en el mismo orden, con `sin_valor=true`. Si EV < −0,08 se añade además `aviso="el modelo ve la cuota corta"`.
7. Si en la banda hay menos de 3 partidos, se publican los que haya (0–2) con `selection_meta.motivo="solo N partidos con cuota 1,30–1,70"`. **La banda no se amplía.**

**Combinada (cuota total 4–6):**
1. Patas posibles: banda 1,25–2,00 (≤ `MAX_RECOMMENDABLE_ODDS`), las 3 mejores líneas por p_cal de cada partido, como máximo 12 partidos, siempre **partidos distintos**.
2. Se enumeran combinaciones de 2 a 4 patas, como máximo unas 50k, con 4 ≤ ∏cuotas ≤ 6.
3. Objetivo: máxima ∏p_cal. Es independencia razonable porque nunca hay dos patas del mismo partido.
4. Se prefieren las de EV_combo = ∏p·∏cuota − 1 ≥ 0. Si no hay ninguna, se toma la de mayor ∏p con `sin_valor=true`.
5. Desempate: menos patas, luego mayor ∏p.
6. Si ninguna combinación llega a 4–6: `combo=null` y `motivo="no se alcanza cuota 4–6 con partidos distintos"`. No se fuerza.

**Congelación.**
- Se escriben `picks_json`, `combo_json`, `picks_sha256` (SHA-256 del JSON canónico), `model_fit_id`, `generated_at` y `rules_version="v2"`.
- Cada pata guarda `model_version`, `p_raw`, `p_model`, `offered_odds`, `odds_captured_at` y `sin_valor`.
- Después solo puede cambiar `grading_snapshot` (lo garantiza el trigger).

**Liquidación.** Con el `grade_single_leg` ampliado. La combinada sale con `overall_from_leg_results`.

---

## 5. Perfil de mercados del usuario

**Qué prioriza y qué no toca.** El perfil prioriza y filtra mercados. **Nunca** cambia `p_model` ni EV: la regla se comprueba con un test.

**Datos de entrada:**
- Patas de `saved_parlays` del usuario, solo de esta temporada.
- Ventana de 120 días con peso exponencial de semivida 45 días.

**Cómo se atribuye el resultado de cada pata:**
- **Simple**: el resultado de la apuesta (`manual_result` si existe, si no el automático).
- **Combinada**: el resultado automático de cada pata en `grading_snapshot.legs`.
  - Si `manual_result="hit"`, todas las patas cuentan como acertadas.
  - Si es `"miss"` y la pata no tiene resultado automático, **se descarta** porque no se puede atribuir.
- No cuentan las nulas (`void`), `pending`, `pending_stats` ni `needs_review`.

**Métricas por familia** (`market_family`: doble oportunidad, tiros a puerta por equipo, tiros a puerta del partido, goles 1ª parte, goles 2ª parte, totales, goles por equipo, btts, h2h, tiros a puerta de jugador…):
- n (ponderado), aciertos y aciertos esperados E = Σ 1/cuota_i, que es la probabilidad implícita. Así no se premia ganar solo por jugar cuotas bajas.
- **Encogimiento** gamma-Poisson hacia 1: `ratio = (aciertos + k) / (E + k)` con k = 20 patas ficticias.
- **Uso**: proporción de patas de la familia con suavizado Dirichlet α = 1.
- **Umbral**: la familia no recibe ningún peso hasta tener n ≥ 15 patas liquidadas. Desde ahí, peso = n / (n + k).
- Banda de cuota habitual por pata (p25–p75) y cuota total habitual de las combinadas (mediana), si hay ≥ 10 combinadas liquidadas.

**Uso en los picks personales.** Mismo grupo congelado de candidatos del día y misma P:
1. Niveles: preferidas (uso ≥ 15 % con peso > 0 y ratio ≥ 1,0) → neutras → evitadas (ratio < 0,85 con n ≥ 15, que **se filtran**).
2. Dentro de cada nivel, p_cal descendente.
3. Mismas reglas de banda, "sin valor" y combinada. La combinada personal usa la cuota habitual del usuario (p. ej. ~3, acotada a [2; 6]) solo si tiene ≥ 10 combinadas liquidadas. Si no, se usa 4–6.
4. Si no hay datos suficientes, los picks personales coinciden con los globales y se indica "aún aprendiendo".

---

## 6. Validación

**Walk-forward por día.** Para cada día D: ajustar con los partidos con saque < 00:00 UTC de D y predecir los de D. Se excluye el calentamiento (hasta que los dos equipos tengan ≥ 3 partidos). Quedan unos **250–300 partidos evaluables**; los tiros a puerta y las mitades se evalúan sobre los ~355 con stats.

**Métricas por familia:**
- log-loss (principal), Brier, ECE con 5 bins y nitidez;
- MAE de λ (goles, tiros a puerta).

**Bases de comparación:**
1. El modelo actual reconstruido sin fuga con `poisson_backtest.run_backtest`.
2. La tasa base.
3. La probabilidad sin margen de **cierre** de Winamax en los 191 partidos con `odds_history`. Cierre = el precio vigente en la última toma con `first_seen_at < commence_time`.

**Simulación de picks** (solo partidos con historial):
- cuota en la "hora de generación simulada" (09:00 de D, desde `odds_history`) frente al cierre;
- CLV = cuota_gen / cuota_cierre − 1, con IC bootstrap;
- acierto frente a la probabilidad implícita media;
- ROI en unidades **solo como métrica interna, nunca en la UI**.

**Elección de hiperparámetros.** Rejilla de ≤ 24 combinaciones (σ, ξ, w_xG, encogimiento de s). Se elige en la ventana A (hasta el 2026-09-24) y se informa en la ventana B (del 2026-09-25 al 2026-10-20), sin reajustar con B.

**Criterio de promoción** (`should_promote`), sobre el mismo conjunto de evaluación:
1. El log-loss agregado de las familias habilitadas baja, y el Δ es < 0 en ≥ 90 % de 1000 remuestreos bootstrap pareados por partido.
2. Ninguna familia habilitada empeora más de un 1 % relativo (no inferioridad).
3. Una familia solo se **habilita** para picks si n ≥ 100 y ECE ≤ 0,05, y además no es peor que la tasa base.
4. CLV medio de la simulación ≥ el de la versión activa menos su error estándar. Es informativo hasta tener ≥ 100 picks simulados; a partir de ahí bloquea.

La promoción es un CLI (`promover_modelo`) con la puerta automática: si no pasa, no se promueve. El reajuste nocturno con los mismos hiperparámetros crea un `model_fit` nuevo, no una versión nueva.

**Forward test real.** `prediction_snapshots` + `odds_history` permiten medir la calibración y el CLV en directo, semana a semana, sin pasar por el backtest.

**Qué se podrá afirmar y qué no:**
- **Sí:** si v2 está mejor calibrado que v1 y que la tasa base; si se acerca al mercado de cierre en 1X2, doble oportunidad y totales; si es razonable una ventaja de campo o una cuota de goles de 2ª parte por liga.
- **No:**
  - Rentabilidad: con unas 300 apuestas a cuota ~1,5 el error estándar del ROI es de unos ±6–7 pp.
  - Diferencias de log-loss menores de ~0,005.
  - Nada fiable sobre UEFA (54 partidos) ni sobre props de jugador.
  - El cierre es aproximado: con 3 tomas al día, la última puede ser de 6–12 h antes del saque.

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| **Fuga de datos** | `cutoff` obligatorio en `load_training_matches` y `assert_sin_fuga`. No se usan `TeamSeasonStats` ni `recent_goal_form` en v2. Bajas fuera. En el backtest, cuotas con `first_seen_at ≤` hora simulada. Test que mete un partido futuro y comprueba que se ignora. |
| **Sobreajuste** | Previos ridge, rejilla pequeña, elección en A e informe en B, Platt con b ≤ 1, familias deshabilitadas por defecto. |
| **EV inflado** | EV con p calibrada. "Sin valor" visible. `MAX_RECOMMENDABLE_ODDS`. La combinada usa la P conjunta real, sin bonificaciones. Picks globales sin props de jugador hasta que pasen la validación. |
| **Correlación en la combinada** | Solo partidos distintos. Nunca dos patas del mismo partido (por ejemplo, doble oportunidad con más de 1,5 goles). |
| **Faltan stats** (45 de 400 partidos sin `match_stats`, xG nulo) | Los modelos de tiros a puerta y xG se entrenan con lo que hay. Equipo con < 3 observaciones → sin P en esa familia. xG solo si la cobertura es ≥ 80 %. Liquidación `pending_stats` → `needs_review`. |
| **UEFA** (pocos datos, rivales de ligas no importadas con solo ~3 partidos) | Fortalezas compartidas con la liga, previo en el desplazamiento de liga, exigir ≥ 5 partidos por equipo. UEFA fuera de los picks hasta que el backtest UEFA tenga n ≥ 100. |
| **Picks manipulables** | Generación solo hoy y solo partidos que aún no han empezado, trigger de inmutabilidad, hash, `model_fit_id`, y adiós a la generación perezosa en GET. |
| **Mercados sin cuota** (2ª parte) | El modelo existe, pero la familia queda fuera de los picks hasta tener el betType (pregunta 1). |

---

## 8. Bloques para el programador (en orden)

Cada bloque: tests en clases `TestXxx` con nombres en español y sin BD; ruff limpio; `graphify update .`; casilla en HOJA_DE_RUTA.

| Bloque | Objetivo y archivos | Tests | Hecho cuando |
|---|---|---|---|
| **B0. Diagnóstico de datos** | CLI `backend/tools/diagnostico_modelo.py` de solo lectura (+ función pura de resumen en `model_dataset.py`). Mide: cobertura de xG, `goals_1h/2h` y tiros a puerta por liga; equipos con partidos UEFA y de liga; mercados presentes en `odds_history` (double_chance, match_sot_totals); `official_data_pipeline` vía `get_settings()`. | `TestResumenCobertura` | Informe pegado en HOJA_DE_RUTA con los datos para decidir xG y UEFA. |
| **B1. Liquidar mercados nuevos** | `saved_parlay_grading.py`, `saved_parlays.py:49-67`, `featured_daily_picks.py:76-96`. Doble oportunidad con el marcador; mitades y tiros a puerta (partido y equipo) con `MatchStats`; estado `pending_stats`. | `TestDobleOportunidad` (1X/X2/12 con nombres de Winamax), `TestSegundaMitad`, `TestTirosAPuertaEquipo`, `TestPendienteDeStats` | Las apuestas de doble oportunidad del usuario se liquidan solas; los 601 tests previos siguen en verde. |
| **B2. Mapear tiros a puerta por equipo** | `winamax.py:82-132` (6206/6207), `winamax_normalization.py:20-38`, etiquetas en `winamax_odds_ingester.py:117-126`. | Parser sobre `data/raw/winamax_muestras/match_72221330_state.json`: 6206 → local (Tottenham) y "Más/Menos" con su línea | El test pasa sin lanzar scrapers. |
| **B3. Dataset sin fuga** | `model_dataset.py` | `TestSinFuga` (partido futuro o no terminado excluido), `TestStatsAusentes` | `load_training_matches` y `assert_sin_fuga` listos. |
| **B4. Ratings Dixon–Coles ridge** | `team_ratings_model.py` (scipy) | Equipo sin partidos → media de la liga; equipo más goleador → λ mayor; datos simétricos → ratings ≈ 0; pesos de decaimiento; ρ dentro de los límites de `_apply_dixon_coles`; determinismo | Ajuste de 400 partidos en < 5 s. |
| **B5. Probabilidades por línea** | `market_probabilities.py` | Doble oportunidad = suma 1X2; totales con ρ; push; mitades consistentes (λ_1ª + λ_2ª = λ); binomial negativa → Poisson con dispersión grande; nombres de doble oportunidad de Winamax | Cubre todas las claves de la sección 3.1. |
| **B6. Tiros a puerta y xG** | `sot_model.py`; mezcla xG en `team_ratings_model.py` | Medias de tiros a puerta razonables; w = 1 equivale al modelo sin xG | Desactivable por hiperparámetro. |
| **B7. Backtest y calibración** | `model_backtest.py`, `model_calibration_v2.py`, CLI nuevo `backend/tools/backtest_modelo.py` (lee BD; se marca obsoleto `backtest_poisson.py`, que depende de Sofascore) | Log-loss y ECE conocidos; `closing_price` (cierre antes del saque, ignora lo posterior); CLV; bootstrap con semilla fija | Informe v1 frente a v2 frente a mercado por familia, ventanas A y B. |
| **B8. Versionado** | `models/model_version.py`, `models/model_fit.py`, migración, `model_registry.py`, CLI `promover_modelo.py` | `TestReglaPromocion` (mejora, no inferioridad, habilitación de familias) | v2 registrada como candidata y promovida solo si pasa. |
| **B9. Integración** | `graded_odds_service.py:703-747` con el ajuste activo detrás de `model_v2_enabled` en `config.py`; `models/prediction_snapshot.py` + migración; paso de reajuste en `daily_refresh.py` | Adaptador: sin ajuste activo → camino legado idéntico | La página de partido muestra P v2 en tiros a puerta y mitades. |
| **B10. Picks v2** | `daily_picks_v2.py`, `featured_daily_picks.py`, `api/featured_daily_picks.py`, `schemas/responses.py:274-292`, migración (columnas + trigger), paso en `daily_refresh.py` | Banda 1,30–1,70; completar con "sin valor"; partidos distintos; combinada 4–6 y caso sin combinada; margen de saque; cuotas viejas; desempate determinista; hash estable; `ensure-date` con fecha pasada → 422 | Una fila v2 congelada al día; las filas v1 siguen sirviendo. |
| **B11. Perfil y picks personales** | `user_market_profile.py`, `models/user_daily_pick.py` + migración, router nuevo `app/api/me_picks.py` | Umbral 15; encogimiento k = 20; decaimiento; atribución de combinadas con resultado manual; **p_model y EV iguales antes y después del reordenado**; usuario sin datos → igual que los globales | Los endpoints responden sin importes. |
| **B12. Frontend** | `pages/transparencia/*`, `pages/mis-apuestas/*`, `core/models.ts`, `core/api.service.ts` | Vitest: insignia "Sin valor", combinada nula, fila v1, estado "pendiente" | Revisión con `make-interfaces-feel-better` (modo rápido). |
| **B13. Documentación** | HOJA_DE_RUTA (fase 3), CLAUDE.md (CLIs nuevos) | — | Casillas al día. |

B1 y B2 son independientes de B3–B9 y se pueden hacer en paralelo. B10 depende de B5 y B8; B11 depende de B1 y B10.

---

## 9. Preguntas para el usuario

1. **Goles en la 2ª parte:** ¿en Winamax apuestas "2ª mitad – Nº total de goles" antes del partido o en directo? Si es antes del partido, necesito una muestra de la página (guardada a mano) con ese mercado para mapear su betType sin lanzar scrapers. Mientras tanto, la familia queda fuera de los picks.
2. **"Tiros a puerta":** ¿por equipo y por partido (6205, 6206, 6207) o también de jugador? Los de jugador quedarían fuera de los picks globales hasta validarlos.
3. **Día de los picks:** ¿día civil en hora de Madrid (con generación a las 09:00) o se mantiene la fecha UTC actual?
4. **Combinada personal:** ¿usamos tu cuota total habitual (~3) en lugar de 4–6 cuando haya ≥ 10 combinadas liquidadas, o la combinada es siempre 4–6?
5. **Promoción:** ¿la promoción de un modelo nuevo la lanzas tú con el CLI (con la puerta automática) o prefieres que sea automática cada noche si pasa la regla?

### Archivos críticos para la implementación
- backend/services/saved_parlay_grading.py
- backend/services/poisson_model.py
- backend/services/graded_odds_service.py
- backend/services/featured_daily_picks.py
- backend/scrapers/winamax.py
