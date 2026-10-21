# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Un único usuario principal con dos papeles a la vez (confirmado 2026-10-16):

- **Apostante que analiza:** el autor del proyecto, que lo usa de verdad antes de apostar en
  Winamax ES. Lo hace en el **ordenador, con calma**, antes de que empiecen los partidos: no es un
  uso rápido desde el móvil.
- **Portfolio:** el mismo proyecto lo verán profesores (2º DAM) y reclutadores. Tiene que dejar
  ver lo que hace y cómo está construido.

## Product Purpose

Ayudar a decidir apuestas de fútbol partido a partido. El usuario entra en un partido, ve con
calma sus estadísticas y decide **combinando los datos con su propio conocimiento de fútbol**; el
modelo es un apoyo, no la autoridad. Con lo que decide **monta combinadas, las guarda y sigue cómo
salen**.

Los dos trabajos principales, por igual:

1. **Analizar un partido**: stats de equipo y jugador (también por mitad), bajas, cuotas de
   Winamax y la probabilidad del modelo con su EV.
2. **Montar y seguir combinadas**: construirlas desde el análisis, guardarlas y ver su resultado.

Éxito: decidir con todo delante sin saltar entre webs (Flashscore, Winamax, notas propias).

## Positioning

Cruza en una sola vista lo que normalmente está separado: las cuotas reales de Winamax ES
(incluidos props de jugador y mercados por mitad), las stats de Flashscore enlazadas al mismo
jugador y equipo, y la probabilidad de un modelo Poisson / Dixon–Coles con su EV. El historial de
cuotas guarda cómo se mueve cada línea hasta el cierre.

## Operating Context

- Datos que se refrescan solos tres veces al día (09/15/21 h) y un botón "Actualizar cuotas" para
  las cuotas de los próximos 5 días.
- Competiciones: top 5 europeas + Champions, Europa League y Conference. Soporte inicial de
  baloncesto.
- Mercados que más interesan al usuario: tiros a puerta de jugador, goles en la 2ª parte, córners
  y tarjetas por mitad.
- Las apuestas se hacen fuera, en Winamax: la app no apuesta ni mueve dinero.

## Capabilities and Constraints

- Angular 21 + Tailwind v4 (frontend), FastAPI + PostgreSQL (backend). Todo en **español**.
- Login con JWT; combinadas guardadas por usuario (`/me/parlays`) con resultado recalculado
  cuando terminan los partidos.
- Páginas enrutadas: login, dashboard, mis apuestas, estadísticas, transparencia. Hay páginas sin
  ruta (`analisis`, `calibration`, `match-detail`, `matches`, `recommendations`, `value-bets`):
  preguntar antes de borrarlas o enrutarlas.
- Las competiciones UEFA aún no tienen P del modelo (sin stats de temporada); se resolverá en la
  fase 3. La interfaz debe convivir con líneas sin P del modelo.
- Cuotas almacenadas y visibles hasta 10,00, pero solo las de hasta 4,00 pueden recomendarse
  (`MAX_RECOMMENDABLE_ODDS`): por encima son informativas y nunca se marcan como valor.

## Brand Commitments

- Nombre: **Bets AI**. Interfaz y textos en español.
- Tema oscuro (compromiso previo del proyecto).
- El usuario pide expresamente una web que **"no parezca hecha por IA"**, con **animaciones** y el
  máximo nivel de oficio. (Requisito del usuario; la dirección visual concreta se decide fuera de
  este documento.)

## Evidence on Hand

- Datos reales en la BD local: partidos, stats de Flashscore, cuotas e historial de Winamax.
- Crítica previa del dashboard: `.impeccable/critique/2026-10-15T08-11-45Z__frontend-src-app-pages-dashboard.md`
  (13/40; P0: el dashboard no muestra EV, P del modelo ni combinadas).
- No hay testimonios, usuarios externos, rendimiento histórico de apuestas ni métricas de
  acierto del modelo publicadas: no inventarlos.

## Product Principles

1. **El partido es el centro.** Todo se organiza alrededor de analizar un encuentro concreto.
2. **El criterio del usuario manda.** El modelo informa (P, EV, confianza); nunca decide ni
   presiona.
3. **Del análisis a la combinada sin fricción.** Lo que se decide en un partido se lleva a la
   combinada sin pasos extra, y se puede seguir después.
4. **Honestidad con los datos.** Si falta un dato (sin cuotas, sin P del modelo, sin stats), se dice
   tal cual; nunca se culpa a los filtros ni se rellena.
5. **Debe enseñar el oficio.** Como portfolio, la calidad de construcción e interacción es parte
   del producto.
