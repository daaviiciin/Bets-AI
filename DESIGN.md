---
name: Bets AI
description: Análisis de cuotas de fútbol contado como una retransmisión deportiva.
colors:
  noche: "#0b1222"
  noche-profunda: "#0e1729"
  panel: "#15203a"
  panel-elevado: "#1c2a4b"
  filete: "#26365c"
  tinta: "#f3f5f9"
  gris-grafismo: "#8f9bb5"
  verde-boletin: "#2fd18a"
  rojo-baja: "#e6463c"
  ambar-duda: "#f2b53a"
typography:
  display:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "3.75rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.01em"
  headline:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "0.01em"
  title:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 800
    lineHeight: 1.5
    letterSpacing: "0.01em"
  body:
    fontFamily: "Barlow, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"tnum\" 1"
  label:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 700
    letterSpacing: "0.02em"
  cifra:
    fontFamily: "Barlow Semi Condensed, Barlow, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    fontFeature: "\"tnum\" 1"
rounded:
  none: "0px"
  full: "9999px"
spacing:
  gutter-movil: "16px"
  gutter: "32px"
  contenedor: "1440px"
  barra: "64px"
components:
  cuota:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.none}"
    width: "64px"
    height: "46px"
  cuota-hover:
    backgroundColor: "{colors.panel-elevado}"
  cuota-en-boletin:
    backgroundColor: "{colors.verde-boletin}"
    textColor: "{colors.noche}"
  filtro:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.gris-grafismo}"
    typography: "{typography.label}"
    rounded: "{rounded.none}"
    padding: "8px 16px"
  filtro-activo:
    backgroundColor: "{colors.tinta}"
    textColor: "{colors.noche}"
  pestana:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.gris-grafismo}"
    typography: "{typography.title}"
    rounded: "{rounded.none}"
    padding: "10px 20px"
  pestana-activa:
    backgroundColor: "{colors.tinta}"
    textColor: "{colors.noche}"
  boton-boletin:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.tinta}"
    typography: "{typography.title}"
    padding: "8px 14px"
  boton-boletin-con-selecciones:
    backgroundColor: "{colors.verde-boletin}"
    textColor: "{colors.noche}"
  linea:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.tinta}"
    rounded: "{rounded.none}"
    padding: "8px 12px"
    height: "56px"
---

# Design System: Bets AI

## Overview

**Creative North Star: "Grafismo de retransmisión"**

Cada partido se cuenta como lo cuenta la tele: un marcador inclinado a todo el ancho pintado con
los colores de los dos clubes, rótulos en cursiva condensada que entran con un barrido y una
noche azul de fondo que no compite con nada. El color no es de la marca: lo ponen los clubes de
cada partido (primario y secundario sacados del escudo). La interfaz solo aporta tres colores
propios con significado fijo: verde para el boletín, rojo para bajas y errores, ámbar para dudas.

Es una herramienta de análisis para usar con calma en el ordenador, densa pero legible: filas de
partido con tres cuotas, pestañas de previa, alineaciones, estadísticas, cuotas y jugadores, y un
boletín lateral persistente. La personalidad está en los detalles de retransmisión (inclinación,
barridos, cifras tabulares), no en decoración. Rechaza explícitamente la lista gris de Flashscore
y el panel oscuro con un acento neón.

**Key Characteristics:**
- Fondo azul noche plano; profundidad por capas tonales, no por sombras.
- Los dos clubes ponen el color de cada pantalla de partido.
- Rótulos en Barlow Semi Condensed cursiva 800 y mayúsculas; texto en Barlow; cifras tabulares.
- Inclinación de rótulo (−8°) reservada a marcador, pestañas y botón del boletín.
- Esquinas rectas en todo salvo escudos y marcas de jugador (círculos).
- Movimiento "entrada en antena": las placas barren con el canto a −8°, los textos suben desde
  su línea base, los filetes se trazan y las cifras cuentan; la cuota vuela al boletín y las
  páginas cambian con una cortinilla. Todo instantáneo con "reducir movimiento".

**Movimiento: entrada en antena.** Cada pieza entra como un grafismo que el realizador pone en
antena, siempre con la misma gramática (clases `tv-*` de `styles.scss` y directivas de
`shared/tv-motion.ts`):
- **Las placas barren** (`tv-barrido`): un corte con el canto inclinado −8° descubre la pieza de
  izquierda a derecha (`tv-placa` si la pieza ya está inclinada).
- **Los textos suben** (`tv-sube`): asoman desde su propia línea base, como tras una máscara.
- **Los filetes se trazan** (`tv-trazo`, `tv-dibuja` en SVG) y **las cifras cuentan**
  (`tvCifra`): del 0 a su valor al aparecer, y del valor viejo al nuevo cuando cambian.
- **El foco de cada página** lleva además un filete guía de tinta que cabalga el canto del
  barrido: el título de Partidos y los dos clubes del marcador.
- **Cortinilla entre páginas** (View Transitions del router): la página nueva barre a todo el
  alto sobre la anterior, hacia la derecha al entrar y hacia la izquierda al volver; la barra
  superior queda quieta y su filete activo viaja de un enlace al siguiente.
- **Tiempos:** entrada completa de una vista ≲ 900 ms, escalonada (30–90 ms entre piezas, con
  tope); interacciones ≤ 200 ms (pestañas, desplegar, ordenar 220 ms). Lo que está bajo el
  pliegue entra al verse, una sola vez (`tvEntra`). En listas largas nunca hay un retardo por
  elemento: un mercado de 40 líneas barre entero. Solo `transform`, `opacity`, `clip-path` y
  `stroke-dashoffset`; ease-out exponencial; los keyframes describen solo el "desde", así el
  contenido se ve aunque falle el JS.

## Colors

Una noche azul casi negra con grises fríos; el color saturado lo aportan los clubes y tres
señales con significado fijo.

### Primary
- **Verde boletín** (verde-boletin): lo que está en el boletín (relleno de la cuota elegida, botón
  del boletín con selecciones) y lo que el modelo marca como candidato (filete interior y EV en
  verde). Nunca decorativo.

### Secondary
- **Colores de club** (dinámicos, del escudo o de `CLUB_COLOR_OVERRIDES`): paneles del marcador,
  franjas del mini marcador, barras comparativas, fichas del campo e iniciales de escudo. Si los
  dos clubes visten parecido, el visitante pasa a su secundario; un club casi negro usa su
  secundario como acento sobre la noche.

### Tertiary
- **Rojo baja** (rojo-baja): jugadores de baja y errores.
- **Ámbar duda** (ambar-duda): jugadores en duda y avisos del boletín (partido repetido).

### Neutral
- **Azul noche** (noche): fondo de página.
- **Noche profunda** (noche-profunda): cajón del boletín.
- **Panel** (panel): superficie por defecto de cuotas, filtros, pestañas y tarjetas.
- **Panel elevado** (panel-elevado): hover y centro oscuro del marcador.
- **Filete** (filete): bordes y separadores de 1 px.
- **Tinta** (tinta): texto principal y placas claras (filtro activo, pestaña activa, centro del
  marcador).
- **Gris grafismo** (gris-grafismo): texto secundario, etiquetas, cifras de apoyo.

### Named Rules
**The Los Clubes Pintan Rule.** La interfaz no tiene color de marca saturado: en una pantalla de
partido el color lo ponen los dos clubes, y en el resto manda la noche azul.

**The Verde Significa Boletín Rule.** El verde solo dice "está en el boletín" (relleno) o "es
candidata" (filete interior). Si algo es verde y no es una de las dos cosas, sobra.

**The Placa Que No Se Funde Rule.** Si un club viste claro (luminancia > 0,45), la placa central
del marcador y del mini marcador pasa a panel-elevado; si no, es tinta. Tres piezas siempre se
leen como tres.

## Typography

**Display Font:** Barlow Semi Condensed (con Barlow, system-ui)
**Body Font:** Barlow (con system-ui)

**Character:** La condensada en cursiva 800 es la voz del rótulo de televisión; Barlow, de la
misma familia, deja el texto corrido limpio y técnico. Todo con cifras tabulares, para que las
cuotas se alineen en columna.

### Hierarchy
- **Display** (800 cursiva, 48–60 px, interlineado 1, mayúsculas): título de página ("Partidos")
  y nombres de club en el marcador (hasta 48 px).
- **Headline** (800 cursiva, 30 px, mayúsculas): cabecera de día en la portada.
- **Title** (800 cursiva, 16 px, mayúsculas): pestañas, botón del boletín, cabeceras de sección.
- **Body** (400–600, 16 px): nombres de equipo, selecciones, texto de apoyo.
- **Label** (700, 13–15 px, mayúsculas, +0,02 em): filtros, navegación, nombres de competición.
- **Cifra** (700, 16 px en filas; en la fila 1X2 del partido, rótulo de 30 px): cuotas, siempre
  con `fmtCuota`.

### Named Rules
**The Rótulo Rule.** Cursiva + mayúsculas + 800 solo para rótulos (títulos, pestañas, marcador,
botón del boletín). El texto que se lee de corrido va en Barlow recta.

## Layout

Contenedor de 1.440 px centrado, con márgenes de 16 px en móvil y 32 px desde 640 px. Barra
superior fija de 64 px (por eso `scroll-padding-top: 88px`). En móvil la navegación pasa a una
fila desplazable bajo la barra y los escudos del marcador se ocultan.

La portada abre con una cartela (título, dos placas y un filete hasta "Actualizar cuotas"), la
regla de la jornada (desde 640 px) y los filtros; después, una lista agrupada por día y
competición: cada fila es mini marcador (62 px móvil, 96 px escritorio) + dos equipos + tres
cuotas a la derecha. La página de partido apila marcador,
fila 1X2 en tres celdas, pestañas y un panel; Cuotas se reparte en dos columnas desde 1.280 px y
el campo de alineaciones se limita a 1.000 px. Ritmo de espaciado en la escala de 4 px de
Tailwind; secciones separadas por 40 px.

## Elevation & Depth

Plano por defecto. La profundidad se cuenta con capas tonales: noche → panel → panel elevado,
separadas por filetes de 1 px. La única sombra es la del cajón del boletín al abrirse, oscura y
difusa hacia la izquierda; no hay brillos ni sombras luminosas.

### Shadow Vocabulary
- **Cajón** (`box-shadow: -24px 0 48px -24px rgb(0 0 0 / 0.6)`): solo el boletín lateral.

### Named Rules
**The Sin Brillos Rule.** Nada brilla: ni halos de color, ni degradados metálicos. El degradado
del panel de club oscurece un tercio un color oscuro, pero solo un 6 % uno claro, para que el
blanco no se vuelva gris. Lo "futurista" del movimiento es precisión, no luz: el filete guía y
el cabezal de escaneo son líneas de tinta sólidas, de 1–3 px y de un solo paso, y la banda de
carga es tonal (panel → panel elevado); ninguno tiene desenfoque ni halo.

## Shapes

Esquinas rectas (0 px) en cuotas, filtros, pestañas, tarjetas y paneles: es un grafismo de tele,
no una app de tarjetas redondeadas. Los círculos (`9999px`) se reservan a escudos, marcas de
jugador en el campo y puntos de forma. La inclinación de −8° (`--skew`) es la silueta propia:
marcador, mini marcador, pestañas y botón del boletín; el contenido se endereza dentro.

### Named Rules
**The Inclinación Escasa Rule.** Solo se inclina lo que en una retransmisión sería un rótulo.
Botones de cuota, filas y tarjetas van rectos.

## Components

### Cuota
Pieza que se pulsa para llevar una selección al boletín: rectangular, densa, con la etiqueta
(1, X, 2) arriba en pequeño y la cifra debajo.
- **Shape:** rectas, 46 × 46 px en móvil y 64 × 46 px desde 640 px.
- **Default:** panel con filete; hover a panel elevado con borde gris; al pulsar escala 0,95.
- **En el boletín:** relleno verde boletín con texto azul noche.
- **Sin cuota:** guion, opacidad 0,45 y cursor bloqueado.

### Línea (pestaña Cuotas)
Versión ancha de la cuota: selección y P(modelo) · EV a la izquierda, cifra a la derecha, 56 px
de alto como mínimo (44 px en la celda compacta de las tablas Más de / Menos de).
- **Celda compacta:** enseña siempre P(modelo) si el modelo la calcula, y el EV solo cuando la
  línea tiene valor.
- **Con valor:** filete verde interior de 2 px y EV en verde.
- **Cuota por encima de 4,00:** solo informativa; nunca se marca como valor (ni filete verde ni
  EV), aunque el modelo dé EV positivo.
- **Sin modelo:** borde discontinuo, apenas más claro que el filete (filete + 35 % de gris).
- **En el boletín:** como la cuota, relleno verde.
- **Movimiento:** las líneas de un mercado barren todas juntas con su mercado al verse; al
  desplegar, solo las nuevas se desenrollan hacia abajo (180 ms).

### Filtros (chips)
- **Style:** placas rectas de panel con texto en gris, mayúsculas 700 y un contador al 60 %.
- **State:** activo en tinta con texto noche. Se desplazan en horizontal si no caben.

### Pestañas
- **Style:** placas inclinadas, rótulo de 16 px; activa en tinta, inactivas en panel.
- **Behavior:** roles ARIA de pestañas con flechas de teclado. La activa se rellena de tinta
  barriendo (200 ms) y la anterior se vacía antes (140 ms); el panel nuevo entra con un barrido
  diagonal corto (0,3 s, ease-out exponencial).

### Navigation
- **Style:** barra fija sobre noche al 95 % con desenfoque, enlaces en label gris; activo en tinta
  con un filete de 3 px pegado al borde inferior. Marca "Bets" + placa inclinada "AI".
- **Mobile:** los enlaces bajan a una fila desplazable bajo la barra.
- **Movimiento:** al arrancar la app la barra se monta una vez (filete inferior trazado, "Bets"
  sube, la placa "AI" barre, los enlaces suben, el botón del boletín barre desde la derecha).
  Entre páginas no se mueve: solo su filete activo viaja al enlace nuevo.

### Cartela de página y regla de la jornada (portada)
- **Cartela:** título en display cursiva que barre con un filete guía de tinta en el canto; a su
  derecha, dos placas inclinadas (tinta y gris, más anchas que altas para no leerse como "II")
  que abren un filete de 1 px hasta el botón "Actualizar cuotas". El total de partidos cuenta.
- **Regla de la jornada:** línea de tiempo de los días con partidos (11–23 h por día) con una
  marca por hora de saque; más alta si coinciden varios saques. El día elegido va en tinta sobre
  una banda de panel; el resto, en gris. Es un grafismo de apoyo (oculto a lectores de
  pantalla: los mismos datos están en filtros y lista). Al aparecer, un cabezal de escaneo la
  recorre y levanta las marcas a su paso; después solo cambian color y altura.

### Marcador (signature)
Tres piezas inclinadas a todo el ancho: club local con degradado hacia su borde exterior, centro
con hora y competición, visitante. Cada club barre desde el centro hacia fuera (0,62 s) con un
filete guía de tinta en el canto; el centro se abre como una persiana desde su línea media y
después suben nombres, escudos, hora y competición (el resultado, si se jugó, cuenta desde 0).
El mini marcador de la portada es su versión pequeña: franjas de club de 18 px (6 px en móvil)
y placa de hora con la misma regla de contraste; en cada fila caen sus franjas y suben sus
cuotas.

### Boletín
Botón inclinado en la barra (verde con nº de selecciones y cuota total) y cajón lateral derecho
de 400 px sobre noche profunda; cierra con Escape. Al pulsar una cuota, una copia vuela hasta el
botón (Web Animations API) y el botón late. Con la primera selección el verde entra barriendo
en el botón; la cuota total cuenta hasta su nuevo valor. Al abrir el cajón, las selecciones
barren desde la derecha una tras otra; las que se quitan salen por la derecha (200 ms).

### Barras comparativas y campo
Barras de estadística en el color de cada club que crecen desde el centro con retardo
escalonado (40 ms por fila, tope 400 ms) mientras sus cifras cuentan desde 0; al cambiar la
muestra (en su campo / toda la temporada) las barras se transforman y las cifras cuentan del
valor viejo al nuevo. Campo en perspectiva con fichas circulares en el color del club; aro rojo
= baja, aro ámbar discontinuo = duda. Al entrar, el césped barre, las líneas se trazan y las
fichas aparecen de cada portería hacia el centro. La forma (V/E/D) gira como las paletas de un
marcador.

## Do's and Don'ts

### Do:
- **Do** usar los tokens `tv-*` de `styles.scss` en código nuevo y pintar con `matchPaint()` todo
  lo que lleve color de club.
- **Do** formatear cuotas, porcentajes y fechas solo con `core/format.ts` y cifras tabulares.
- **Do** dar a cada animación su versión con "reducir movimiento" (estado final visible sin
  esperar retardos).
- **Do** hacer entrar lo nuevo con la gramática de antena (barre, sube, se traza, cuenta) y
  reservar el filete guía al foco de cada página.
- **Do** señalar el estado también por forma (filete interior, borde discontinuo, aro), no solo
  por color.

### Don't:
- **Don't** añadir un color de acento propio (cian, neón, morado): el color lo ponen los clubes.
- **Don't** usar el verde para algo que no sea el boletín o una candidata del modelo.
- **Don't** redondear esquinas de cuotas, tarjetas o pestañas.
- **Don't** inclinar filas, tarjetas ni botones de cuota.
- **Don't** usar brillos, halos o sombras luminosas.
- **Don't** usar el "aparece subiendo y desvaneciéndose" genérico, ni escalonar línea a línea
  listas largas, ni hacer esperar al usuario: ninguna vista tarda más de ~0,9 s en estar entera.
- **Don't** construir pantallas nuevas con la paleta antigua `brand-*` / `slate-*`.
