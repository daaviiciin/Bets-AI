# Investigación: modelos de fútbol y herramientas de apuestas (fase 3)

**2026-10-25.** Qué hacen otras librerías, artículos, webs y bots que resuelven lo mismo que Bets AI
(probabilidad por mercado → comparar con la cuota → elegir picks), y qué conviene replicar. El plan
de implementación está en [PLAN_FASE_3.md](PLAN_FASE_3.md) y el estado, en
[HOJA_DE_RUTA.md](HOJA_DE_RUTA.md). Fuentes al final.

## 1. Conclusiones (lo que cambia o confirma el plan)

| # | Qué | Por qué | Dónde encaja |
|---|---|---|---|
| 1 | **Calibrar antes que acertar.** Elegir el modelo por calibración (log-loss, ECE), no por % de acierto | Walsh y Joshi (2024): seleccionar por calibración dio un ROI medio de +34,7 % frente a −35,2 % al hacerlo por acierto. Hoy el Poisson actual sale **muy sobreconfiado**: en la simulación del 27/10 da P = 0,418 a una combinada de cuota 4,11 (el mercado le da ~0,24), un EV de +72 % | B7–B8 (ya en el plan). **Subir prioridad** |
| 2 | **Dixon–Coles con encogimiento y decaimiento temporal** como núcleo | Es lo que implementan penaltyblog y goalmodel; con 8–10 partidos por equipo el encogimiento (ridge / previo jerárquico) es lo que más mejora | B4 (ya en el plan) |
| 3 | **Validar nuestro ajuste contra penaltyblog** con los mismos partidos | Librería madura (Cython, mismos modelos): si nuestros ratings y λ salen distintos, el fallo es nuestro. Solo como script de comprobación, no como dependencia de la API | B4–B5 (nuevo) |
| 4 | **Probar Binomial negativa y "Weibull + cópula"** solo como candidatas | Corrigen la sobredispersión de los goles, pero son más lentas y no siempre compensan; deciden el backtest | B7 (candidatas) |
| 5 | **Pi-ratings como segunda línea base** (barata) | Superan a Elo en fútbol y son la mejor variable de los modelos de ML del 2017 Soccer Prediction Challenge | B7 (nuevo: base de comparación) |
| 6 | **Mezclar con la cuota sin margen del mercado** (Shin o "power") | Es lo que hacen todos los bots de value betting (con Pinnacle) y Opta (cuotas + power rankings); Egidi, Pauli y Torelli (2018) lo formalizan. Sin una casa afilada, la de Winamax sin margen sirve de previo, no de verdad | v3 (ya en el plan como candidata) |
| 7 | **El CLV es el KPI principal**, no el % de acierto | Con ~300 picks a cuota ~1,5 el error del ROI es de ±6–7 pp; el CLV converge mucho antes | B7 y Transparencia (ya en el plan) |
| 8 | **ML (XGBoost, CatBoost) no compensa todavía** | Los buenos resultados (RPS 0,19–0,21) salen de muchas temporadas de datos; aquí hay una sola | Descartado por ahora (decisión del 21/10) |
| 9 | **Mitades con la cuota de goles de la 2ª parte por liga** | En nuestros datos es 0,585 (de 0,561 en Serie A a 0,621 en Bundesliga); ya se usa en "Mitad con más goles" | B5 (hecho en global; falta por liga) |
| 10 | **Tiros a puerta: modelo propio en cadena** (tiros → a puerta → goles) | Egidi y otros modelan los tres niveles; para la línea basta una binomial negativa por equipo | B6 (ya en el plan) |

## 2. Librerías y repositorios

### penaltyblog (Python, el más completo)

Biblioteca de modelado de fútbol con implementaciones en Cython. Lo que tiene y nos sirve:

- **Modelos de goles:** `PoissonGoalsModel`, `DixonColesGoalsModel`, Poisson bivariante, Poisson con
  ceros inflados, Binomial negativa, "Weibull Count + Copula", y modelos bayesianos (simple y
  jerárquico, con encogimiento automático entre equipos de varias ligas).
- **Decaimiento temporal en todos:** `dixon_coles_weights(dates, xi=...)` → `weights`. Es la misma
  idea que el hiperparámetro ξ del plan (B4).
- **Ratings:** Elo, Massey, Colley y Pi (`pb.ratings.PiRatingSystem()`, con
  `calculate_match_probabilities` y `update_ratings`).
- **Probabilidades implícitas:** quita el margen de la casa con varios métodos, Shin entre ellos.
- **Utilidades de apuesta:** probabilidades de hándicap asiático, más/menos y 1X2 desde la matriz de
  marcadores; módulo de backtest.
- **Scrapers:** FBRef, Understat, Club Elo, Fantasy Premier League (no los necesitamos).

Uso recomendado aquí: **referencia para validar** nuestros ajustes (mismo dataset, comparar ratings,
ρ y log-loss), no dependencia de producción.

### goalmodel (R, opisthokonta)

Poisson, Dixon–Coles y Binomial negativa, combinables (p. ej. ajuste Dixon–Coles sobre la Binomial
negativa), con predicción de goles esperados y más/menos. Útil por su blog: explica con datos reales
cuándo la Binomial negativa mejora a Poisson y cómo estimar ρ por separado.

### footBayes (R)

Modelos frecuentistas y bayesianos en Stan, con fuerzas de ataque y defensa que varían en el tiempo.
Es la versión "de libro" de lo que el plan hace con decaimiento + encogimiento.

### sports-betting (Python, georgedouzas, ~700 estrellas)

Caja de herramientas al estilo scikit-learn: `SoccerDataLoader` (datos de football-data.co.uk),
"bettors" (`ClassifierBettor`, `OddsComparisonBettor`) que detectan value bets, y `backtest` con
búsqueda de hiperparámetros (`BettorGridSearchCV`). Interesa su **forma de evaluar**: backtest por
fechas sin fuga y comparación con las cuotas, igual que B7.

### Otros

- **OddsHarvester:** scraper de oddsportal.com (cuotas históricas de muchas casas). Sería la vía para
  tener cierres de una casa afilada, pero es otro scraper con riesgo de bloqueo: no por ahora.
- **FootballGPT/football-model, afrodevXG:** proyectos pequeños que comparan su P con la cuota. Sus
  resultados son auto-declarados; no aportan nada que no esté en lo anterior.

## 3. Modelos de goles: qué hay y qué nos conviene

| Modelo | Qué añade | Para nosotros |
|---|---|---|
| Poisson independiente (Maher 1982) | Ataque, defensa y campo | Es el `legacy-v1` actual (sin encogimiento) |
| Dixon–Coles (1997) | Corrige 0-0, 1-0, 0-1, 1-1 con ρ | Núcleo de v2. Hoy ρ = 0 en la práctica (no hay informes de calibración) |
| Poisson bivariante | Correlación entre los goles de los dos equipos | Candidata; más lenta y difícil de interpretar |
| Poisson con ceros inflados | Más 0-0 | Poco útil: el ρ de Dixon–Coles ya cubre los 0-0 |
| Binomial negativa | Sobredispersión (varianza > media) | Candidata en goles; **la buena para tiros a puerta** |
| Weibull + cópula | Otra distribución y dependencia | Candidata solo si el backtest la justifica |
| Bayesiano jerárquico | Encogimiento entre equipos y ligas, incertidumbre | Es lo que el plan aproxima con ridge (más barato) |

**Datos que pesan más con pocos partidos:** el encogimiento hacia la media de la liga y el **xG**.
FiveThirtyEight (modelo SPI, hoy archivado) usaba tres medidas por partido: goles ajustados, xG de
tiros y xG "sin tiro" (acciones cerca del área), y daba a cada equipo un rating ofensivo y uno
defensivo en goles frente a un rival medio en campo neutral. Nosotros tenemos xG en el 100 % de los
partidos de liga (B0), así que la mezcla goles–xG del plan (mejora 5) es viable; el peso lo decide el
backtest.

## 4. Ratings dinámicos

- **Elo (ClubElo):** un número por club que se actualiza tras cada partido según el resultado frente
  a lo esperado. Simple y sirve para comparar ligas. (Su página de metodología respondió 403 a la
  consulta automática; no se ha podido leer el detalle.)
- **Pi-ratings (Constantinou y Fenton, 2013):** ratings separados de local y visitante, centrados en
  0 y actualizados con la diferencia de goles con rendimientos decrecientes (un 5-0 cuenta más que un
  1-0, pero no cinco veces más); un rating de +1 ≈ un gol mejor que la media. Superaron a dos
  variantes de Elo y fueron rentables contra el mercado en cinco temporadas de la Premier.
- **Uso aquí:** como **base de comparación** en el backtest (si v2 no le gana, algo va mal) y, más
  adelante, como variable del modelo de picks.

## 5. Machine learning

En el 2017 Soccer Prediction Challenge ganaron modelos de árboles sobre **ratings como variables**:
XGBoost sobre pi-ratings (RPS 0,2063, 52,4 % de acierto), k-NN + XGBoost sobre ratings y recencia
(Berrar y otros) y, después, CatBoost sobre pi-ratings (RPS 0,1925, 55,8 %). Todos usan muchas
temporadas y ligas. Con una sola temporada, el modelo estadístico con encogimiento es más fiable; el
ML queda para cuando haya 2–3 temporadas propias.

## 6. Qué hacen las webs y los bots

- **Bots de value betting** (OddsJam, RebelBetting, SharpAPI…): no modelan el partido. Toman la cuota
  de una casa afilada (Pinnacle), le quitan el margen y la tratan como probabilidad "real"; una cuota
  de otra casa por encima es valor (EV = cuota / cuota justa − 1). Con solo Winamax no podemos copiar
  esto tal cual, pero sí usar su cuota sin margen como **previo** (punto 6 de las conclusiones).
- **Opta "supercomputer":** combina cuotas de apuestas con sus Power Rankings para la P de cada
  partido y simula el resto de la temporada miles de veces. Lo importante: **usa el mercado**.
- **Forebet:** "algoritmos y ML sobre datos desde el año 2000"; no publica el método.
- **FiveThirtyEight SPI:** ver punto 3 (archivado desde 2025).

**Cómo quitar el margen:** el método proporcional (dividir cada 1/cuota por la suma) es el habitual,
pero deja el sesgo favorito–outsider: las casas cargan más margen al empate y al outsider. **Shin** y
el método "power" lo corrigen mejor en mercados desequilibrados (penaltyblog y el paquete `implied`
de R los implementan). Para la mezcla de v3 conviene Shin o power, no el proporcional.

## 7. Evaluación: calibración y CLV

- **Calibración > acierto** (Walsh y Joshi, 2024; NBA, una temporada de apuestas): un modelo que dice
  70 % debe acertar el 70 % de las veces. El plan ya lo trata con Platt acotado (b ∈ [0,5; 1], solo
  puede bajar la confianza), ECE ≤ 0,05 para habilitar una familia de mercados y log-loss como métrica
  principal.
- **CLV (closing line value):** batir la cuota de cierre es la mejor señal temprana de ventaja; el
  resultado de las apuestas es muy ruidoso. En mercados líquidos batir el cierre no siempre se
  convierte en beneficio, pero en casas "blandas" como Winamax es el mejor indicador adelantado. El
  historial de cuotas (fase 1, bloque 2) ya lo permite; falta mostrarlo en Transparencia.

## 8. Mercados concretos

- **Mitad con más goles (3317):** λ₁ = λ·(1 − s), λ₂ = λ·s con Poisson independientes. Con s = 0,585 y
  3,09 goles por partido el modelo da 1ª 27,2 % · igual 23,0 % · 2ª 49,8 %; en los datos ha salido
  26 % · 23 % · 51 % (383 partidos). Winamax paga "2ª mitad" a ~2,15 (46,5 %). Hecho el 2026-10-25;
  falta s por liga (B5).
- **Tiros a puerta (equipo y partido):** Binomial negativa por equipo con ataque y defensa de tiros a
  puerta encogidos; el del partido, por suma (convolución). Alternativa de la literatura: tiros ~
  Poisson/NB y a puerta ~ Binomial(tiros, p).
- **Tiros a puerta de jugador:** tasa por 90 minutos × minutos esperados, ajustada por el rival.
  Cuidado con el sesgo de supervivencia: los que más juegan son los mejores tiradores (StatsBomb), así
  que la tasa por 90 de los suplentes no se extrapola a 90 minutos.

## 9. Propuesta de orden (para decidir)

1. **B3–B4** como estaban (dataset sin fuga + Dixon–Coles ridge con decaimiento), con el **script de
   validación contra penaltyblog**.
2. **B7 antes de lo previsto** (backtest + calibración), con tres bases: legacy-v1, pi-ratings y la
   cuota de cierre sin margen (Shin). Motivo: el legacy está sobreconfiado y los picks de hoy salen de
   él.
3. **B5–B6** (mercados por línea, tiros a puerta y mitades por liga).
4. **B8–B9** (versionado y paso a producción), y después la mezcla con el mercado (v3).

## Fuentes

- penaltyblog: [documentación](https://penaltyblog.readthedocs.io/en/latest/index.html), [modelos](https://penaltyblog.readthedocs.io/en/latest/models/overview.html), [pi-ratings](https://penaltyblog.readthedocs.io/en/latest/ratings/pi.html), [probabilidades implícitas](https://penaltyblog.readthedocs.io/en/latest/implied/index.html), [PyPI](https://pypi.org/project/penaltyblog/)
- goalmodel: [GitHub](https://github.com/opisthokonta/goalmodel), [presentación en R-bloggers](https://www.r-bloggers.com/2018/10/introducing-the-goalmodel-r-package/)
- footBayes y comparación de modelos: [Egidi y Torelli](https://arts.units.it/bitstream/11368/2961005/8/Egidi-Torelli_ComparingGoal-BasedAndResult-B-Post_print.pdf), [Stochastic modelling of football matches](https://arxiv.org/pdf/2312.04338)
- sports-betting: [PyPI](https://pypi.org/project/sports-betting/)
- OddsHarvester: [gittrend](https://gittrend.io/repo/jordantete/OddsHarvester); FootballGPT: [espejo del repo](https://proxy-dev.blitzz.co/proxy/123456/github.com/FootballGPT/football-model); afrodevXG: [ficha](https://images.glama.ai/mcp/servers/ellisegrant/afrodevXG)
- FiveThirtyEight: [cómo funcionan sus predicciones de clubes](https://fivethirtyeight.com/features/how-our-club-soccer-predictions-work), [xG sin tiro (Tomkins Times)](https://tomkinstimes.com/2017/10/shot-vs-non-shot-expected-goals/)
- Pi-ratings: [Constantinou (QMUL)](https://qmro.qmul.ac.uk/xmlui/bitstream/123456789/10776/5/CONSTANTINOU%20Determing%20the%20Level%20of%20Ability%202012%20Accepted.pdf), [paquete piratings (CRAN)](https://cran.r-project.org/web/packages/piratings/refman/piratings.html), [revisión de ML en deportes de equipo](https://arxiv.org/pdf/1912.11762)
- ML: [Machine Learning for Soccer Match Result Prediction](https://arxiv.org/pdf/2403.07669), [Berrar y otros](https://oro.open.ac.uk/96634), [Yeung y otros (2024)](https://mlanthology.org/mlj/2024/yeung2024mlj-evaluating), [comparativa de modelos (xG Football Club)](https://thexgfootballclub.substack.com/p/which-machine-learning-models-perform)
- Calibración: [Walsh y Joshi (2024)](https://researchportal.bath.ac.uk/en/publications/machine-learning-for-sports-betting-should-model-selection-be-bas/), [OpticOdds](https://opticodds.com/blog/calibration-the-key-to-smarter-sports-betting)
- Mercado en el modelo: [Egidi, Pauli y Torelli (2018)](https://arxiv.org/abs/1802.08848), [Opta](https://theanalyst.com/articles/opta-football-predictions)
- Value betting y margen: [SharpAPI: cuotas sin margen](https://sharpapi.io/learn/what-is-no-vig-odds), [SharpAPI: +EV](https://sharpapi.io/features/ev-betting-api), [paquete implied (R)](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html), [OddsPapi: tres métodos](https://oddspapi.io/blog/?p=3089), [Karl Whelan: sesgo favorito–outsider](https://www.karlwhelan.com/Teaching/BettingMarkets/10.EfficienciesandFavourite-LongshotBias.pdf)
- CLV: [SharpAPI: CLV histórico](https://docs.sharpapi.io/en/api-reference/historical-clv/), [Game Theory Picks](https://gametheorypicks.substack.com/p/what-is-closing-line-value)
- Tiros: [Forecasting football matches by predicting match statistics](https://arxiv.org/pdf/2001.09097), [Asian handicap y redes bayesianas](https://arxiv.org/pdf/2003.09384), [ESANN 2015](https://www.esann.org/sites/default/files/proceedings/legacy/es2015-126.pdf), [StatsBomb: minutos y tiros](https://blogarchive.statsbomb.com/articles/soccer/premier-league-forwards-2008-13-time-on-pitch-shots-and-goals/)
- Dixon–Coles con xG: [The Cutback](https://the-cutback.beehiiv.com/p/simulating-football-with-dixon-coles-a-deep-dive-into-xg-data-and-predictive-modeling), [The Conversation](https://theconversation.com/a-brief-history-of-statistics-in-football-why-actual-goals-remain-king-in-predicting-who-will-win-194314)
