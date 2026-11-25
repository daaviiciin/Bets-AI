# Investigación: errores de cuotas entre bet365 y Winamax

**2026-11-13.** Qué es un «error de cuota», qué pasa si apuestas a uno en España, cómo se detectan,
de dónde sacar las cuotas de bet365 y cómo encajaría todo en Bets AI. Solo es investigación y
diseño: aún no hay código. Va después del plan de la fase 3 ([PLAN_FASE_3.md](PLAN_FASE_3.md)) y no
lo sustituye. Las fuentes están enlazadas en cada sección y al final.

Leyenda: **[C]** comprobado en una fuente primaria (términos de la casa, BOE, tribunal, documentación
de la API, artículo científico). **[F]** opinión de foros o blogs comerciales, sin verificar.
**[I]** inferencia mía.

## 0. Resumen (la conclusión sincera)

1. Los **errores palpables** (cuota mal tecleada, lados cambiados, línea equivocada) **no dan dinero**:
   Winamax **anula** la apuesta [C] y bet365 la **mantiene a la cuota corregida** [C], que es una
   apuesta normal con margen, o sea, EV negativo.
2. Winamax define el error manifiesto, entre otras cosas, por ser una cuota «sustancialmente superior
   (…) a las ofrecidas (…) por el resto de operadores nacionales» [C]. **Lo que el detector marcaría
   como error grande es justo lo que la casa puede anular.**
3. Lo que sí puede tener valor son las **discrepancias moderadas** (3–15 %), en especial las **cuotas
   desfasadas**: una casa que no se ha movido cuando el mercado sí. No llegan a «manifiestas» y se
   pagan.
4. Con **solo dos casas blandas** se puede detectar con fiabilidad que **no coinciden** y si hay
   **surebet** (es aritmética), pero **no cuál de las dos se equivoca**. Para eso hace falta una
   **referencia afilada** (Pinnacle o Betfair Exchange) o, en peor caso, el consenso de varias casas.
5. Las **surebets** entre dos casas blandas son raras, duran poco y tienen un riesgo: si una pata se
   anula como error, la otra queda descubierta. Con tres refrescos al día casi no se verán.
6. El **value betting frente a una referencia afilada** es lo único con evidencia seria de beneficio
   (Kaunitz y otros, 2017 [C]), pero **a las casas no les gusta**: limitan las cuentas que ganan. En
   España hay sentencias a favor del jugador (AP Cantabria 2019, no firme [C]), pero reclamar es
   lento y caro. **Hay que contar con que te limiten** a los pocos meses si funciona.
7. **No hay que scrapear bet365**: es la casa con el anti-bot más agresivo [F] y sus términos le
   permiten bloquear el acceso automatizado [C]. Hay APIs de terceros que ya traen **bet365 ES** y
   **Winamax ES**, y algunas también **Pinnacle**.
8. **Vía recomendada:** OddsPapi (gratis, 250 peticiones al mes, con bet365 ES, Winamax ES, Pinnacle y
   Betfair Exchange [C según su web]) para un **registro en seco** de pocos partidos al día. Si
   funciona, se valora pagar odds-api.io (de 65 a 129 $ al mes) o el plan de pago de OddsPapi.
9. El valor real para Bets AI es doble: (a) **una referencia afilada mejora el modelo** (la mezcla
   con el mercado de la v3) y el **CLV**, y (b) una pantalla de **«mejor cuota y discrepancias»**
   que ayude a elegir dónde apostar. Como fuente de ingresos, el rédito esperado es **pequeño y se
   acaba** con la limitación.
10. Antes de fiarse de nada: **registro en seco + CLV** durante al menos unas 300 detecciones.

---

## 1. Qué es un «error de cuota» y cuáles se pueden aprovechar

| Tipo | Qué es | Ejemplo | Cómo se ve | ¿Se puede aprovechar? |
|---|---|---|---|---|
| **Error palpable / manifiesto** | Fallo humano o informático en el precio o en el mercado | 1X2 local a 12,0 cuando todo el mercado da 1,25; lados cambiados (local ↔ visitante); hándicap −1,5 publicado como +1,5; total 3,5 con la cuota de 2,5 | Ratio enorme frente a la referencia (×1,5 o más), o la cuota coincide con la del *otro* lado o la *otra* línea en el resto de casas | **No.** Winamax anula; bet365 recalcula a la cuota corregida (ver §2). Como mucho, no pierdes nada |
| **Cuota desfasada (*stale*)** | La casa tarda en reaccionar a una noticia (alineación, lesión) o a un movimiento del mercado | Sale la alineación sin el delantero titular, el resto de casas bajan el «marca» y una lo mantiene 20 minutos | La referencia y otras casas se movieron hace X minutos y esta no (se ve en `odds_history`) | **Sí, a veces.** Es la ventaja clásica de *value*. Dura minutos; antes del partido no se anula (no es «error manifiesto»), salvo que la diferencia sea enorme |
| **Discrepancia de opinión o de margen** | Dos casas valoran distinto o cobran márgenes distintos, sin error | Winamax da más a los favoritos y bet365 a los empates | Diferencia pequeña y estable, dentro de la dispersión habitual del mercado | **Casi nunca.** Es ruido; solo sirve para elegir la mejor cuota cuando ya ibas a apostar |
| **Surebet (arbitraje)** | Combinando casas, la suma de 1/cuota de todos los resultados es menor que 1: se gana pase lo que pase | Más de 2,5 a 2,10 en una y menos de 2,5 a 2,05 en otra: 1/2,10 + 1/2,05 = 0,964 → +3,7 % | Aritmética exacta sobre las mejores cuotas de cada resultado | **Sí, en teoría.** En la práctica: margen de 1–3 %, dura poco, una pata puede anularse por error, y es lo que más rápido limita una cuenta |
| **Value bet frente a referencia afilada** | La cuota blanda supera la probabilidad «justa» de una casa afilada sin margen | Pinnacle sin margen da P = 0,50; Winamax paga 2,15 → EV = 2,15 × 0,50 − 1 = +7,5 % | `EV = cuota × P_ref − 1 > umbral` | **Sí.** Es lo único con evidencia académica de beneficio (Kaunitz y otros 2017). Varianza alta; requiere volumen; limitación de cuenta |

Notas:
- La línea entre «desfasada» y «manifiesta» la decide la casa con su propia definición (§2). Una
  diferencia del 5–10 % es una discrepancia normal; una del 50 % es un error. **[I]** Cuanto más
  grande es la ventaja aparente, más probable es que sea un error y que se anule.
- Las combinadas (las que juega el usuario) **suman** los márgenes de cada pata y multiplican el
  riesgo de anulación: si se anula una pata, Winamax la cuenta a 1,00 [C].

## 2. Qué pasa cuando apuestas a un error (términos y regulación en España)

### 2.1 bet365 España ([Condiciones generales](https://help.bet365.es/s/es-es/terms-and-conditions)) [C]

Cito literalmente:

- **6.1:** «Se considerará que usted ha actuado de mala fe deliberadamente y, en consecuencia,
  bet365 no será responsable en caso de que intente obtener ventaja de cualquier error relacionado
  con las apuestas, incluidos los casos en los que: (i) exista un "Error obvio" en las cuotas/el
  hándicap/los totales (…)».
- **6.2 Cuota incorrecta:** «en el caso de que se detecte antes del comienzo, en directo o después de
  un evento, cualquier apuesta prevalecerá y se determinará a la cuota revisada de bet365. Se
  anularán las apuestas si las cuotas revisadas son menores de 1.001».
- **6.3 Número incorrecto/Líneas/Hándicap/Total:** la apuesta prevalece «al número, línea, spread,
  hándicap o total con la cuota revisada de bet365».
- **6.6 / 6.7:** encuentro o mercado mal indicado → se anulan todas las apuestas.
- **6.9:** tope de 10.000 € de ganancia en una situación de «Error obvio».
- Sobre automatización: «bet365 se reserva el derecho a bloquear el acceso a jugadores siempre que
  tenga sospechas sobre actividad de juego automatizado o efectuado mediante robots» y limita «el
  acceso diario de cada cuenta a 50 por día» (contexto exacto de esa cifra no verificado).

**Consecuencia:** en bet365 un error de cuota no se anula, **se recalcula a su cuota corregida**,
también si se detecta después del partido. Es una apuesta normal a su precio: EV negativo.

### 2.2 Winamax España ([Reglamento de apuestas deportivas](https://www.winamax.es/terminos-y-condiciones-apuestas-deportivas)) [C]

- **Error manifiesto:** «si como consecuencia de un error humano, informático o tecnológico la cuota
  de una apuesta se ve afectada por un error manifiesto, Winamax informará al jugador (…) en todo
  caso antes de que comience el evento (…) a excepción de las apuestas "live" (…) y procederá a
  anular la apuesta. Se entenderá que concurre dicho error cuando la cuota sea manifiestamente
  superior o inferior a la que debiera haber sido en función del riesgo asumido (…) **y cuando dicha
  cuota sea sustancialmente superior o inferior a las cuotas ofrecidas para el mismo mercado por el
  resto de operadores nacionales**».
- **Error en el título o la descripción:** se anula la apuesta.
- **Live:** puede anular las apuestas que aprovechen «el desajuste entre la realidad de los
  acontecimientos (…) y la cuota publicada».
- **Límites:** «Winamax establece el número máximo de apuestas o la cantidad máxima que es posible
  apostar en cada apuesta en función de su exposición al riesgo financiero» y fija límites según el
  perfil del jugador (art. 21.2 de sus Términos).
- **Robots:** «El jugador se compromete a no utilizar robots o programas informáticos que permitan la
  realización de apuestas automáticas» (habla de **apostar**, no de leer cuotas).
- **Liquidación de fútbol:** «sobre la base del tiempo reglamentario, incluido el tiempo añadido. Las
  prórrogas o potenciales tandas de penalti no están incluidas», con estadísticas del organizador
  oficial o, si no hay, de Opta.
- **Selección anulada en una combinada:** cuenta como ganadora a 1,00.

**Consecuencia:** en Winamax, antes del partido, el error manifiesto se **anula**. Para eso tienen
que darse dos condiciones a la vez: la cuota no se corresponde con la probabilidad y además se
separa mucho del resto de casas españolas. Una cuota desfasada un 5 % no lo cumple.

Hay un caso público reciente: un tuit de @WinamaxDeportes de noviembre dice «Se pagarán las apuestas
ganadoras; solo se anularán las apuestas realizadas a partir del 1 de noviembre por error
manifiesto» ([enlace](https://x.com/WinamaxDeportes/status/2119456558237270507)). No he podido abrir
el hilo para ver el contexto.

### 2.3 Regulación española

- **Orden EHA/3080/2011** (reglamentación básica de las apuestas deportivas de contrapartida),
  [BOE](https://www.boe.es/buscar/act.php?id=BOE-A-2011-17964) [C]. Art. 13: cada operador fija en
  sus **reglas particulares** los casos de anulación. El importe de una apuesta anulada «será
  devuelto (…) siempre sin ningún coste ni obligación adicional para los participantes». La Orden
  **no define** el error de cuota: lo deja a las reglas particulares de cada casa, que aprueba la DGOJ.
- **Definición «tipo» en las reglas particulares españolas** (Sportium, y casi igual en Marca
  Apuestas) [C]: «Error manifiesto en la cuota: cuando (…) resulte objetivamente apreciable que la
  cuota ofrecida no mantiene correspondencia con el riesgo real del resultado pronosticado», a la
  vista de las cuotas de otros operadores con licencia en España, las probabilidades estadísticas y
  la divergencia respecto del resto del mercado regulado. Si no procede la anulación, «las ganancias
  se liquidarán conforme a lo que debería haber sido la cuota correcta».
  ([Sportium](https://www.sportium.es/reglasapuestas/reglasparticularesapuestasdeportivas),
  [Marca Apuestas](https://www.marcaapuestas.es/reglas)).
- **Limitación de cuentas:** la **Audiencia Provincial de Cantabria** (sección 2.ª, 28/06/2019, no
  firme) obligó a una casa a quitar las restricciones a un jugador que ganaba por encima de la media:
  ganar más no es causa válida si no hay fraude, y una cláusula que permite cerrar la cuenta «por
  cualquier motivo» deja el contrato «al arbitrio de uno de los contratantes»
  ([CGPJ](https://www.poderjudicial.es/cgpj/es/Poder-Judicial/Noticias-Judiciales/Una-casa-de-apuestas-debera-levantar-la-restriccion-a-un-jugador-online-con-ganancias-superiores-a-la-media)) [C].
  Hay más sentencias de primera instancia en la misma línea (2021, [Confilegal](https://confilegal.com/20210727-la-justicia-ordena-a-dos-casas-de-apuestas-a-no-limitar-el-derecho-del-usuario-para-seguir-apostando-cuando-esta-en-racha/)).
  Pero las casas siguen limitando, sobre todo bajando el importe máximo por apuesta, que sí cabe en
  sus reglas («en función de su exposición al riesgo») ([El Confidencial, 2022](https://www.elconfidencial.com/tecnologia/2022-06-25/casas-apuestas-online-ganadores-dinero-truco_3448679/),
  [Andersen](https://es.andersen.com/blog/2022/03/14/casas-de-apuestas-prohibido-ganar/)). No he
  encontrado ninguna resolución de la DGOJ que se pronuncie sobre esto.

### 2.4 Cuánto rédito queda, sin adornos

| Estrategia | Ventaja típica | Vida útil | Riesgo principal |
|---|---|---|---|
| Error palpable | 0 (anulada o recalculada) | — | Perder tiempo; en bet365, la apuesta recalculada se queda viva con EV negativo |
| Cuota desfasada | 3–10 % por apuesta [F] | Minutos | Llegar tarde (con 3 refrescos al día casi siempre) |
| Surebet entre dos blandas | 1–3 % sin riesgo de resultado [F] | Minutos | Que se anule una pata; limitación rápida |
| Value frente a Pinnacle | 2–5 % de media [F] | Meses, hasta que te limitan | Varianza; limitación |

Kaunitz, Zhong y Kreiner (2017) ganaron dinero con *value* frente al consenso de mercado en 10 años
simulados, 6 meses con cuotas minuto a minuto y 5 meses con dinero real, y cuentan cómo las casas
les **limitaron** ([arXiv:1710.02824](https://arxiv.org/abs/1710.02824)) [C]. **[I]** Para el
usuario, que juega combinadas de 2 a 5 €, una ventaja del 3 % son céntimos por boleto. El valor
real está en **no apostar donde el precio es peor** y en **mejorar el modelo**, no en «cazar
errores».

## 3. Cómo se detectan en la práctica

### 3.1 Quitar el margen

Con cuotas `o_i` de un mercado completo, `q_i = 1/o_i` y `S = Σ q_i` (el *overround*):

| Método | Fórmula | Cuándo |
|---|---|---|
| Multiplicativo | `p_i = q_i / S` | Rápido; sobrevalora a los poco probables |
| Power (el que ya usamos, `sin_margen_power`) | `p_i = q_i^k`, con `k` tal que `Σ p_i = 1` | Corrige el sesgo favorito–outsider; bueno para mercados de dos lados |
| Shin | `p_i = (√(z² + 4(1−z)·q_i²/S) − z) / (2(1−z))`, con `z` tal que `Σ p_i = 1` | El más preciso en 1X2 (Štrumbelj 2014 [C]); `z` ≈ proporción de dinero informado |

Štrumbelj (2014, *International Journal of Forecasting* 30, 934–943) muestra que las probabilidades
de Shin predicen mejor que la normalización simple o la regresión, y que las diferencias entre casas
se reducen cuando el mercado es más grande ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533)) [C].
Con una casa afilada (margen del 2–3 % [F]) el método importa poco; con dos blandas (5–8 % [F])
importa bastante. **El margen de Winamax y el de bet365 se pueden medir con nuestros datos**
(`bookmaker_margin`) en vez de suponerlo.

### 3.2 La referencia: contra qué comparar

| Referencia | Fiabilidad | Comentario |
|---|---|---|
| **Pinnacle sin margen** | Alta en las líneas principales | El estándar de los bots de *value*. Su API pública **cerró el 23/07/2025** ([repo oficial](https://github.com/pinnacleapi/pinnacleapi-documentation)) [C]; hoy solo llega a través de terceros |
| **Betfair Exchange** (punto medio entre *back* y *lay*, descontando comisión) | Alta si hay liquidez | El exchange **español** tiene una bolsa de liquidez propia, mucho menor que la internacional [F] ([WinnerOdds](https://winnerodds.com/es/casas-de-apuestas-de-intercambio/)). Hay API para cuentas españolas ([Betfair](https://support.developer.betfair.com/hc/en-us/articles/12403084077468-How-can-I-bet-via-the-API-on-the-Betfair-Spanish-Exchange)) [C] |
| **Consenso** (mediana sin margen de N casas) | Media; mejora con N | Es lo que hacen Kaunitz y otros. Con N ≥ 5 casas es usable |
| **Las dos blandas** (bet365 contra Winamax) | Baja | Solo dice que no coinciden, no quién falla |
| **Nuestro modelo** (Dixon–Coles v2) | Baja-media | Calibrado pero con una sola temporada; sirve de desempate, no de juez |

### 3.3 Medidas de discrepancia

- **Ventaja frente a la referencia:** `edge = o_X · p_ref − 1`.
- **Discrepancia entre dos casas en la misma selección** (sin quitar margen):
  `d = ln(o_A / o_B)`. Es simétrica y no depende del método para quitar el margen.
- **Z robusta por familia de mercado y tramo de cuota:**
  `z = (d − mediana_m) / (1,4826 · MAD_m)`, con la mediana y la MAD (desviación absoluta mediana)
  calculadas sobre los últimos 60 días de pares (A, B) de esa familia (1X2, goles, BTTS, córners,
  props…) y tramo de cuota (<1,5, 1,5–2,5, 2,5–5, >5). Esto evita marcar como raro algo que en esa
  familia es habitual (los props siempre discrepan más).
- **Surebet:** `Σ_i 1 / max_casa(o_i) < 1`; margen = `1 − Σ`.

### 3.4 Frescura (sin esto todo es ruido)

Dos cuotas tomadas con minutos de diferencia **no son comparables**: es la causa número uno de
falsos positivos en arbitraje [F]. Reglas:

- `|t_A − t_B| ≤ 10 min` y la cuota no puede tener más de 20 minutos (5 y 10 minutos si falta menos
  de una hora para el partido).
- Fuera los partidos ya empezados y los que empiezan en menos de 5 minutos.
- Si la fuente da `last_update` de la casa (las APIs lo dan), usar ese, no la hora de nuestra lectura.

### 3.5 Emparejar mercados y líneas entre casas

Solo se comparan líneas con **las mismas reglas de liquidación**:

| Riesgo | Ejemplo | Regla |
|---|---|---|
| Tiempo de juego | 90 min frente a «incluida la prórroga» (copas) | Winamax: solo tiempo reglamentario [C]. bet365: no verificado → mirar sus reglas de fútbol antes de comparar copas |
| Jugador que no sale o entra de suplente | Goleador o tiros: nula frente a perdida | **No verificado en ninguna de las dos.** Hasta comprobarlo, no se comparan props de jugador |
| Fuente de la estadística | Córners o tiros: Opta frente a otro proveedor | Winamax: organizador oficial, si no Opta [C] |
| Línea asiática frente a europea | Total 2,25 frente a 2,5; hándicap asiático frente a europeo | Comparar solo líneas idénticas (mismo `outcome_point` y misma familia) |
| Tarjetas | Puntos (amarilla 10, roja 25) frente a número de tarjetas; cuentan o no las del banquillo | Familias distintas; no mezclar |

### 3.6 CLV como validación

`CLV = o_tomada / o_cierre_justa − 1`, donde `o_cierre_justa = 1 / p_ref_cierre` (la referencia sin
margen en su último precio antes del partido). Si una «discrepancia» tiene CLV medio positivo con
cientos de casos, era real; si no, era ruido o un error que la casa corrigió. Ya está en el plan de
la fase 3 como KPI principal ([INVESTIGACION_MODELOS.md](INVESTIGACION_MODELOS.md) §1, punto 7).

### 3.7 Proyectos y literatura serios

- **Kaunitz y otros 2017**: *value* frente al consenso; con código y datos públicos [C].
- **Štrumbelj 2014**: métodos para quitar el margen (Shin) [C].
- **penaltyblog** (Python) e **implied** (R, Lindstrøm): implementan Shin y power [C, citados en
  INVESTIGACION_MODELOS.md y Academia.edu].
- Los motores comerciales de *value* y *arb* (odds-api.io, OddsJam, RebelBetting) usan el mismo
  esquema: precio justo afilado, frescura y `minProfit` [C, changelog de odds-api.io], pero su
  código no es abierto.

## 4. Con solo bet365 + Winamax: qué es fiable y qué no

| Detección | ¿Fiable solo con dos casas? | Qué hace falta |
|---|---|---|
| «Estas dos casas no coinciden» (z alta) | **Sí** | Historial para la mediana y la MAD por familia |
| Surebet | **Sí** (es aritmética) | Frescura estricta; no hay forma de saber si una pata se anulará |
| Lados cambiados o línea equivocada (error palpable) | **Bastante**: el patrón (la cuota coincide con el *otro* lado o la *otra* línea) es claro | Comprobar contra el modelo propio |
| **Quién** tiene la cuota mala | **No** | Tercera referencia (Pinnacle, Betfair o consenso) |
| Cuota desfasada | **Débil**: se ve que una se movió y la otra no, pero no si la que se movió acertó | Referencia afilada y refrescos frecuentes |
| Value | **No** (entre dos blandas lo normal es que las dos estén por debajo del precio justo) | Referencia afilada |

**Recomendación:** añadir **Pinnacle** como referencia (a través de una API de terceros) y, si es
gratis, **Betfair Exchange**. **[I]** Además, Pinnacle sin margen mejora directamente dos piezas de
la fase 3: la mezcla con el mercado de la v3 (hoy se usa Winamax sin margen, y el plan ya dice que
«sirve de previo, no de verdad») y el CLV, que es más fiable frente al cierre de Pinnacle que frente
al de Winamax.

## 5. Cómo conseguir las cuotas de bet365

### 5.1 Scrapear bet365: no

- **Anti-bot:** es conocida por ser de las más difíciles: código ofuscado, detección de DevTools,
  WebSocket propio, bloqueo de IPs de centros de datos [F]
  ([Reddit r/webscraping](https://www.reddit.com/r/webscraping/comments/1jozhpu/whats_the_weirdest_antiscraping_way_youve_ever/),
  [r/algobetting](https://www.reddit.com/r/algobetting/comments/1nxprwy/any_reliable_bet365_odds_api_out_there/)).
- **Términos:** permiten bloquear el acceso si sospechan de actividad automatizada [C]. No encontré
  una cláusula literal sobre *scraping* de datos (la página es muy larga y no la pude leer entera).
- **Riesgo:** bloqueo de IP (que afectaría también a la navegación normal del usuario desde casa) y,
  si se hace con sesión iniciada, de la cuenta. Además, mantenerlo costaría mucho más que el
  scraper de Winamax.
- Las reglas del proyecto (CLAUDE.md) ya piden no lanzar scrapers en bucle.

### 5.2 APIs de terceros

| Proveedor | bet365 ES | Winamax ES | Pinnacle / Betfair Ex. | Gratis | De pago | Notas |
|---|---|---|---|---|---|---|
| **OddsPapi** | Sí (`bet365.es`, 234–245 mercados por partido medidos el 02/11/2026; **solo antes del partido**) [C, su web] | Sí (`winamax.es`) [C] | Sí, las dos [C] | **250 peticiones al mes**, todas las casas, histórico incluido (máx. 3 casas por llamada) [C] | No pude leer los precios (la página no los mostró) | Una petición devuelve todas las casas de un partido. Props «donde estén disponibles» |
| **odds-api.io** | Sí (añadido el 27/10/2026, antes del partido y en directo) [C] | Sí (añadido el 12/10/2026, antes del partido) [C] | Betfair Ex. y «ON Sharp» solo de pago; **Pinnacle no aparece** en el changelog [C] | 500 peticiones al día, **sin bet365 desde el 26/10/2026** ni casas afiladas [C] | Solo 65 $/mes (2 casas), Starter 129 $ (5), Growth 239 $ (10), Pro 299 $ (15); 5.000 peticiones/hora; WebSocket al doble [C] | Nombres de jugador unificados entre casas (10/11/2026); endpoints de *value* y *arbitrage* |
| **The Odds API** | **No** (solo `bet365_au`) [C] | **No** (solo `winamax_fr` y `winamax_de`) [C] | Sí, las dos [C] | 500 créditos al mes [C] | Desde 30 $ [F] | **Ya se usó y se retiró** el 2026-10-28 (HOJA_DE_RUTA, «Depuración»). El repo no explica por qué; **[I]** lo más probable es que no cubra ni Winamax ES ni bet365 de Europa |
| SportsGameOdds, OpticOdds, Goalserve | Sin verificar | Sin verificar | Pinnacle en SportsGameOdds [F] | SportsGameOdds: 2.500 objetos/mes [F] | 99–149 $ o a medida [F] | Enfocados a EE. UU. o B2B |
| **Betfair Exchange API** (oficial) | — | — | Exchange ES [C] | Clave «delayed» gratis [F] | — | Hace falta cuenta española; liquidez baja en España [F] |

### 5.3 Recomendación

1. **Fase de prueba con OddsPapi gratis.** Con 250 peticiones al mes (unas 8 al día) se cubren 6–8
   partidos al día, una vez por la mañana y otra cerca del inicio, con bet365 ES, Winamax ES,
   Pinnacle y Betfair a la vez. Es suficiente para el registro en seco, no para producción.
2. **Seguir con nuestro scraper de Winamax** como fuente principal de Winamax: es más rico (props
   enlazados con los jugadores de Flashscore) y ya está hecho. La Winamax de la API sirve para
   comprobar que el emparejado funciona.
3. **Si el registro en seco da CLV positivo**, decidir si se paga: odds-api.io Starter (129 $, 5
   casas, pero sin Pinnacle confirmado) o el plan de pago de OddsPapi (precio sin verificar).
4. Antes de integrar nada, **leer los términos de uso de la API** (uso personal o comercial,
   redistribución). No los he podido verificar.

**Pros:** sin riesgo de bloqueo; trae la referencia afilada; histórico gratis para validar.
**Contras:** 250 peticiones se quedan muy cortas; dependencia de un tercero cuyas fuentes no son
oficiales; latencia sin verificar; los props pueden no estar o no emparejarse.

---

## 6. Propuesta de diseño para Bets AI

### 6.1 Fuentes

| Casa | `bookmakers.key` | Rol | Fuente |
|---|---|---|---|
| Winamax ES | `winamax_es` (ya existe) | Blanda (donde apuesta el usuario) | Scraper propio (como hoy) |
| bet365 ES | `bet365_es` | Blanda | API (OddsPapi) |
| Pinnacle | `pinnacle` | **Referencia** | API (OddsPapi) |
| Betfair Exchange | `betfair_ex` | Referencia secundaria | API (OddsPapi), si trae *back* y *lay* |

### 6.2 Cambios en el modelo de datos

El diseño actual ya es multicasa: `Odds` y `OddsHistory` llevan `bookmaker_id`, `market_id`,
`player_id`, `outcome_name` y `outcome_point`, y `Snapshot.source` existe. **Basta con el `bookmaker`
actual** más estos añadidos:

| Cambio | Por qué |
|---|---|
| `bookmakers.role` (`soft` / `sharp` / `exchange`) | Para elegir la referencia sin escribir claves a mano en el código |
| Tabla **`external_event_ids`** (`match_id`, `source`, `external_id`, `matched_at`, `match_score`), única por (`source`, `external_id`) | `Match.external_id` es el de Flashscore; cada API trae sus propios ids de partido. Se rellena con `team_name_matching` + `commence_time` (±15 min) |
| Tabla **`external_player_ids`** (`player_id`, `source`, `external_name`) | Para los props; reutiliza `PlayerResolver`. Solo cuando se verifiquen las reglas de los props |
| **Normalización canónica de `outcome_name`** común a todas las casas | Hoy las claves de mercado ya son neutrales (`h2h`, `totals`, `btts`, `double_chance`… «alineadas con Odds API»), y `winamax_normalization` guarda `Draw` / nombre del equipo en BD, `Over`/`Under` y `Yes`/`No`. Pero **los mercados sin normalización propia guardan la etiqueta de Winamax tal cual** (`normalize_match_outcome`): córners, tarjetas, mitades y props tienen que normalizarse antes de poder compararlos. El cliente de la API traduce a esas mismas claves |
| `odds_history`: usar el `last_update` de la casa cuando exista | Frescura real (§3.4) |
| Tabla **`market_rules`** o un diccionario en código `EQUIVALENCIAS_MERCADO[(casa, market_key)] → regla de liquidación` | Solo se comparan mercados con la misma regla (§3.5) |
| Tabla nueva **`odds_discrepancies`** (los hallazgos): `match_id`, `market_id`, `player_id`, `outcome_name`, `outcome_point`, `bookmaker_id` (la casa con la cuota anómala), `price`, `price_seen_at`, `ref_bookmaker_id`, `ref_kind` (`sharp` / `exchange` / `consensus` / `model` / `pair`), `p_ref`, `devig_method`, `edge`, `z`, `surebet_margin`, `clase`, `confianza`, `detected_at`, `closing_price`, `closing_p_ref`, `clv`, `settled_result`, `voided` | El registro en seco y la pantalla; el CLV se rellena al cerrar el partido |

Todo con migración Alembic revisada, como dicen las convenciones del proyecto.

### 6.3 Algoritmo de detección, paso a paso

Se ejecuta tras cada ingesta (Winamax o API), por partido:

1. **Reunir** las cuotas vigentes por clave de línea `(match_id, market_key, player_id, outcome,
   point)` y casa, con su `seen_at`.
2. **Filtrar por frescura** (§3.4) y por equivalencia de reglas (§3.5). Sin pares válidos → fin.
3. **Quitar el margen** por casa y mercado completo: Shin en 1X2 y power en los de dos lados (ya
   está `sin_margen_power`). Los mercados incompletos no se usan como referencia.
4. **Elegir la referencia** `p_ref` en este orden: Pinnacle → Betfair (punto medio con comisión)
   → consenso (mediana de ≥ 3 casas) → modelo v2 → ninguna (solo par). Se guarda `ref_kind`.
5. **Calcular**, por cada casa blanda X: `edge_X = o_X · p_ref − 1`; para el par Winamax/bet365,
   `d = ln(o_W / o_B)` y la `z` robusta por familia y tramo (§3.3; mientras haya menos de 200 pares
   en la familia, usar una MAD por defecto de 0,04, sin calibrar).
6. **Buscar surebets** sobre las mejores cuotas de cada resultado: `m = 1 − Σ 1/max(o_i)`.
7. **Clasificar** (primera regla que se cumpla):

| Clase | Regla inicial | Confianza | Qué hace la app |
|---|---|---|---|
| **Error probable** | `o_X / o_ref_justa ≥ 1,5` (o ≤ 0,67), o `|z| ≥ 6`, o patrón de lado o línea cambiada: `|o_X(lado) / o_ref(otro lado) − 1| < 3 %` mientras `|o_X(lado)/o_ref(lado) − 1| > 30 %` | Alta si coincide el patrón; media si solo es por tamaño | Avisa: «probable error, la casa puede anularlo o recalcularlo». **No se recomienda apostar** |
| **Surebet** | `m ≥ 1 %`, ninguna pata es un «error probable» y las dos cuotas son frescas | Alta si `|t_A − t_B| ≤ 2 min`; media si ≤ 10 | Muestra el reparto de importes; avisa del riesgo de anulación |
| **Cuota desfasada** | La referencia (o ≥ 2 casas) se movió ≥ 4 % en P en los últimos 60 min, X no ha cambiado desde antes de ese movimiento y `edge_X ≥ 2 %` | Media; alta si `ref_kind = sharp` | Destacada, con su antigüedad |
| **Value** | `edge_X ≥ 3 %` con `ref_kind = sharp`; ≥ 5 % con consenso; ≥ 8 % con modelo (y entonces confianza baja); y `2 ≤ z < 6` cuando haya par | Alta / media / baja según `ref_kind` | En la lista |
| **Ruido** | Todo lo demás | — | No se guarda (o solo una muestra para calcular la MAD) |

8. **Guardar** en `odds_discrepancies` (un hallazgo por línea y clase; si ya existe y sigue igual,
   no se duplica).
9. **Al cerrar el partido:** rellenar `closing_price`, `closing_p_ref`, `clv`, el resultado
   liquidado y si se anuló (anotación manual del usuario, si le pasa).

Todos los umbrales son **iniciales**: se ajustan con el registro en seco (bloque E6).

Nota sobre la frecuencia: con los refrescos actuales (09/15/21 h) **no se verán surebets ni cuotas
desfasadas de minutos**, solo discrepancias que duren horas (sobre todo en props y ligas
menores). Para lo demás haría falta refrescar cerca del inicio (por ejemplo, 60 y 15 minutos
antes) o un WebSocket de pago. Es una decisión del usuario (§7).

### 6.4 Validación antes de fiarse

1. **Registro en seco** de al menos 4 semanas o 300 hallazgos de *value* o desfase, sin apostar.
2. **Métricas por clase y por `ref_kind`:** CLV medio con IC del 95 % (bootstrap), % de CLV
   positivo, ROI teórico a 1 unidad con IC, % de hallazgos que «se cerraron» y **quién se movió**
   (si fue bet365 o Winamax la que fue hacia la otra, eso dice cuál estaba mal).
3. **Criterio para mostrar «value» como recomendación:** CLV medio > +1 % con el IC por encima de 0.
   Si no se cumple, la pantalla se queda como comparador de cuotas, sin recomendar.
4. Mostrarlo en **Transparencia**, como ya se hace con la calibración.

### 6.5 Cómo se vería en la app

- **Página nueva «Comparador» (`/comparador`, `title: 'Comparador · Bets AI'`)**, con los tokens de
  marca y diseñada con `impeccable`:
  - Tabla por hallazgo: partido y hora, mercado y línea, **Winamax / bet365 / referencia sin
    margen**, ventaja, clase (con etiqueta de color), confianza y **antigüedad de cada cuota**
    («hace 3 min»).
  - Filtros: clase, confianza, mercado, «solo mis mercados» (activado por defecto: sin menos goles
    ni «ambos marcan: No», como en el perfil del usuario), mínimo de ventaja.
  - Detalle de la fila: gráfico del historial de las dos casas y de la referencia (de
    `odds_history`) para ver quién se movió y cuándo; reglas de liquidación de cada casa; aviso
    legal de anulación cuando la clase es «error probable».
- **Dashboard:** en cada línea de `graded-odds`, una marca de «mejor en bet365 (+4 %)» cuando la
  misma línea está mejor pagada en la otra casa. Es lo más útil para el día a día del usuario.
- **Transparencia:** sección «Comparador: CLV de los hallazgos» (§6.4).

### 6.6 Bloques (en orden)

| Bloque | Qué | Hecho cuando |
|---|---|---|
| **E0** | Decisiones del usuario (§7) y lectura de los términos de uso de la API elegida | Decisiones anotadas en HOJA_DE_RUTA |
| **E1** | Cliente de la API (`scrapers/oddspapi.py` o similar) + parser con tests sobre un JSON de ejemplo guardado (sin llamar a la API en los tests) | Tests en verde con un JSON real de un partido de LaLiga |
| **E2** | `external_event_ids` + emparejado de partidos con Flashscore (`team_name_matching` + hora) | ≥ 95 % de los partidos de 5 ligas emparejados en una muestra y 0 falsos emparejamientos revisados a mano |
| **E3** | Normalización canónica de mercados y resultados para la API (1X2, doble oportunidad, más/menos goles del partido, BTTS) + `EQUIVALENCIAS_MERCADO` | Las mismas líneas de Winamax por API y por nuestro scraper salen con la misma clave y cuotas iguales (±0,01) |
| **E4** | Ingesta de bet365 ES y Pinnacle en `odds` / `odds_history` (con `bookmakers.role` y migración), dentro del presupuesto de peticiones | Una tanda diaria guarda las tres casas en 6–8 partidos sin pasarse del cupo |
| **E5** | Servicio puro `services/discrepancias.py` (pasos 3–7 de §6.3) + tests (`TestClasificacion`: lados cambiados, surebet, desfase, ruido) | Tests en verde con casos sintéticos, incluidos los extremos |
| **E6** | Tabla `odds_discrepancies` + registro en seco + relleno de cierre y CLV al terminar el partido | 4 semanas o 300 hallazgos guardados con su CLV |
| **E7** | Informe del registro en seco (script manual, como `backtest_modelo`) + ajuste de umbrales | Tabla por clase y `ref_kind` con CLV ± IC; umbrales revisados y anotados |
| **E8** | Endpoint `GET /comparador` + página Angular + marca de «mejor cuota» en el dashboard | La página muestra los hallazgos del día con filtros y detalle; repaso con `make-interfaces-feel-better` y `break-ui` |
| **E9** (opcional) | Pinnacle sin margen como previo de la mezcla de la v3 y como cierre para el CLV de los picks | Backtest con y sin Pinnacle en el mismo periodo |
| **E10** (opcional) | Props de jugador (tiros, goleador) cuando se verifiquen las reglas de «no sale» de las dos casas | Reglas citadas en este documento y `external_player_ids` con ≥ 90 % de emparejados |

## 7. Decisiones que tiene que tomar el usuario

| # | Decisión | Opciones | Recomendación |
|---|---|---|---|
| 1 | **Cuándo** se hace | (a) Después de cerrar la fase 3; (b) en paralelo, solo E0–E4 (datos), porque Pinnacle ayuda a la fase 3 | **(b) solo la ingesta**, y el comparador cuando acabe la fase 3 |
| 2 | **Fuente** de bet365 | (a) OddsPapi gratis; (b) odds-api.io de pago (65–129 $/mes); (c) scraper propio | **(a)** para probar; **nunca (c)** |
| 3 | **Referencia afilada** | (a) Pinnacle a través de la API; (b) también Betfair Exchange; (c) ninguna (solo par + modelo) | **(a)**; (b) si sale gratis en la misma llamada |
| 4 | **Objetivo** de la pantalla | (a) Comparador de mejor cuota para lo que ya juega; (b) buscador de *value* y surebets para apostar por ello | **(a)** primero; (b) solo si el registro en seco da CLV positivo |
| 5 | **Frecuencia** de refresco | (a) La actual (3 al día); (b) +2 tandas cerca del inicio para los partidos del día; (c) tiempo real (de pago) | **(b)** cuando el cupo lo permita; (c) no compensa |
| 6 | **Mercados** del primer alcance | (a) 1X2, doble oportunidad, goles y BTTS; (b) también córners y tarjetas; (c) también props | **(a)**: son los que juega y los de reglas más claras |
| 7 | **Riesgo de limitación** | ¿Acepta que, si lo usa para apostar sistemáticamente al *value*, Winamax o bet365 le limiten la cuenta? | Que lo sepa antes de E8; por eso la recomendación es (a) en la decisión 4 |

## 8. Lo que no he podido verificar

- Precios y términos de uso de OddsPapi (la página de precios no se cargó), su latencia real y si
  trae props de bet365 ES y Winamax ES en las ligas que seguimos.
- Si odds-api.io incluye Pinnacle (en su changelog no aparece).
- Las reglas de fútbol de bet365 (prórroga, jugador que no sale) y las de props de Winamax para el
  jugador que no participa (su reglamento es tan largo que no se pudo leer entero).
- Una cláusula literal de bet365 sobre *scraping* o extracción de datos, y el contexto exacto del
  límite de «50 por día».
- El contexto del tuit de Winamax sobre anulaciones del 1 de noviembre.
- La liquidez actual del exchange español de Betfair y si su API trae *back* y *lay* gratis.
- Resoluciones de la DGOJ sobre errores de cuota o limitación de cuentas (no encontré ninguna).
- El motivo exacto por el que se retiró The Odds API (el repo no lo dice).
- Las cifras de margen típico y de ventaja por estrategia marcadas como [F].

## 9. El comparador de cuotas de Flashscore (reconocimiento del 2026-11-13)

La pestaña «Cuotas» de la página de partido de Flashscore devuelve, con **una sola petición
GraphQL por partido**, todas las casas y todos los mercados de ese partido. Lo lee
`backend/scrapers/flashscore_cuotas.py`; lo guarda `backend/services/comparador_cuotas_ingester.py`.

- **URL**: `https://global.ds.lsapp.eu/odds/pq_graphql?_hash=oce&eventId=<id>&projectId=13&geoIpCode=ES&geoIpSubdivisionCode=ESMD`.
  `projectId=13` es el proyecto que ya usamos para bajas y stats; `eventId` es el id de
  Flashscore del partido.
- **Casas** con `geoIpCode=ES`: bet365, Winamax.es, Sportium.es, 1xBet.es, Versus.es, Betway,
  Retabet, Interwetten, Codere, bwin.es, 888sport, Speedybet.es y William Hill.
- **Respuesta**: `data.findOddsByEventId.odds` es una lista de
  `{bookmakerId, bettingType, bettingScope, odds: [item…]}`. Cada `item` trae `value` (cuota
  actual), `opening` (apertura), `active` y, según el mercado, `eventParticipantId`,
  `handicap.value`, `selection` (`OVER`/`UNDER`/`ODD`/`EVEN`), `bothTeamsToScore`, `score`
  (`"2:1"`) o `winner` (`"1/X"`). Tras el partido, `result` (`WON`/`LOST`/…).
- **No necesita** la cabecera `x-fsign`.
- **No trae** hora de actualización por casa: la frescura es la de nuestra lectura (todas las
  casas de una respuesta salen del mismo instante).
- `eventParticipantId` es el participante del evento, no el id del equipo: el local es el
  primero de la lista del 1X2 (`[local, visitante, empate]` en todas las casas observadas).
- El scraper traduce a las mismas claves neutrales que Winamax (`h2h`, `totals`, `btts`,
  `double_chance`…) y guarda los hándicaps siempre desde el punto de vista del local.

**Estado (2026-11-17):** la ingesta va en el refresh desde el 2026-11-17 (tras las cuotas de
Winamax y antes de los picks, en la tarea programada y en el refresh al abrir la app).

**Estado (2026-11-18):** el detector está conectado. Decisiones del usuario: se hace ya, con el
comparador de Flashscore (no OddsPapi), y se marca la ventaja **de 3 a 15 %** frente al consenso
(«con cantidades pequeñas no es significativa, con grandes sí»); por encima del 15 % es posible
error (§2.2). `backend/services/ventaja_mercado.py` compara la cuota de Winamax de nuestro scraper
con la mediana sin margen de ≥ 3 casas del comparador leídas a ≤ 2 h de ella; la página de partido
lo enseña. Primera medida real (40 partidos, 1.285 líneas): Winamax paga de mediana un 5,8 % menos
que el consenso y ninguna línea cae en el baremo. **Sigue sin referencia afilada** (§0.4): el
consenso es de casas blandas, así que una marca dice que Winamax paga más que el resto, no que
sea *value* de verdad. Falta el registro en seco con CLV (E6–E7) antes de fiarse.

## Fuentes

**Términos y regulación**
- bet365 España, Condiciones generales: https://help.bet365.es/s/es-es/terms-and-conditions
- Winamax España, reglamento de apuestas deportivas: https://www.winamax.es/terminos-y-condiciones-apuestas-deportivas
- Winamax España, reglamento de otras apuestas de contrapartida (6.8): https://www.winamax.es/reglamento-de-otras-apuestas-de-contrapartida
- Orden EHA/3080/2011 (BOE): https://www.boe.es/buscar/act.php?id=BOE-A-2011-17964
- Sportium, reglas particulares: https://www.sportium.es/reglasapuestas/reglasparticularesapuestasdeportivas
- Marca Apuestas, reglas: https://www.marcaapuestas.es/reglas
- CGPJ, AP Cantabria 2019: https://www.poderjudicial.es/cgpj/es/Poder-Judicial/Noticias-Judiciales/Una-casa-de-apuestas-debera-levantar-la-restriccion-a-un-jugador-online-con-ganancias-superiores-a-la-media
- Confilegal 2021: https://confilegal.com/20210727-la-justicia-ordena-a-dos-casas-de-apuestas-a-no-limitar-el-derecho-del-usuario-para-seguir-apostando-cuando-esta-en-racha/
- Andersen, «Casas de apuestas: prohibido ganar»: https://es.andersen.com/blog/2022/03/14/casas-de-apuestas-prohibido-ganar/
- El Confidencial 2022: https://www.elconfidencial.com/tecnologia/2022-06-25/casas-apuestas-online-ganadores-dinero-truco_3448679/
- Tuit de Winamax Deportes: https://x.com/WinamaxDeportes/status/2119456558237270507

**Literatura**
- Kaunitz, Zhong y Kreiner (2017), *Beating the bookies with their own numbers*: https://arxiv.org/abs/1710.02824
- Štrumbelj (2014), *On determining probability forecasts from betting odds*: https://www.sciencedirect.com/science/article/abs/pii/S0169207014000533

**APIs y datos**
- Pinnacle API (cerrada el 23/07/2025): https://github.com/pinnacleapi/pinnacleapi-documentation
- OddsPapi, bet365 ES: https://oddspapi.io/sportsbooks/bet365-es · Winamax ES: https://oddspapi.io/sportsbooks/winamax-es · plan gratuito: https://oddspapi.io/blog/free-odds-api-350-bookmakers/
- odds-api.io, precios: https://odds-api.io/pricing · changelog: https://docs.odds-api.io/changelog · casas: https://odds-api.io/sportsbooks
- The Odds API, casas: https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
- Betfair, API con el exchange español: https://support.developer.betfair.com/hc/en-us/articles/12403084077468-How-can-I-bet-via-the-API-on-the-Betfair-Spanish-Exchange
- WinnerOdds, liquidez del exchange español: https://winnerodds.com/es/casas-de-apuestas-de-intercambio/
- Foros sobre el anti-bot de bet365: https://www.reddit.com/r/webscraping/comments/1jozhpu/whats_the_weirdest_antiscraping_way_youve_ever/ · https://www.reddit.com/r/algobetting/comments/1nxprwy/any_reliable_bet365_odds_api_out_there/
