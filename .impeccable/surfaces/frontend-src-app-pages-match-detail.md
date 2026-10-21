---
version: 1
slug: "frontend-src-app-pages-match-detail"
primary_target: "frontend/src/app/pages/match-detail"
related_targets: ["frontend/src/app/pages/dashboard","frontend/src/app/app.html"]
---

# Surface brief · Partidos + Partido + Boletín

Modo: Operate. Usuario: el autor, en el ordenador y con calma antes de la jornada (además, portfolio).
Trabajo: elegir partido de los próximos 5 días, entenderlo (once, medias casa/fuera, mitades, forma,
bajas, jugadores, cuotas con P del modelo y EV) y llevar líneas al boletín para guardarlas en Mis apuestas.
Rutas: `/dashboard` (lista de partidos), `/partido/:id` (página de partido, sobre `pages/match-detail`),
boletín en el shell (`app.html`). Datos: `GET /matches/{id}/preview`, `/dashboard/graded-odds?match_id=`.
Fuera de alcance: Mis apuestas, Estadísticas, Transparencia y Login (heredan el shell); modelo de fase 3.
Reservado: botón "Mejores cuotas" en la cabecera del partido (fase 3, deshabilitado).
Decisiones pendientes: ninguna de producto; colores de club = escudo + `CLUB_COLOR_OVERRIDES`.

## Direction contract

THESIS: El partido contado como una retransmisión: cada dato entra en pantalla como un rótulo de TV
pintado con los colores de los dos clubes. Rechaza la lista gris de Flashscore y el panel oscuro con un
acento neón.

OWN-WORLD: Azul noche #0B1222, paneles #17233F, filetes #26365C, texto #F3F5F9, gris #8F9BB5. Los dos
clubes ponen el color (primario/secundario del escudo) en marcador, barras, fichas y campo. Verde #2FD18A
solo para lo que entra o está en el boletín; rojo #E6463C para bajas y errores. Barlow Semi Condensed
(rótulos en cursiva 800, mayúsculas) + Barlow para texto; cifras tabulares. Inclinación de -8° solo en
marcador, pestañas y botón de boletín. Sin brillos ni sombras luminosas.

STORY: Veo la jornada, entro en un partido, lo entiendo de un vistazo y bajo al detalle por pestañas;
lo que me convence va al boletín y lo guardo.

FIRST VIEWPORT: Barra superior (marca, Partidos, Mis apuestas, Estadísticas, Transparencia; a la derecha
el botón Boletín con nº de selecciones y cuota). Marcador inclinado a todo el ancho: club local en su
color con escudo a la izquierda, centro con hora y competición (claro; oscuro si un club tiene panel
claro), visitante a la derecha. La jornada NO se muestra: no está en los datos (Flashscore la trae en
`AC` y no se guarda). Debajo, fila 1X2 de tres celdas (la que tiene valor, con filete verde interior y
el EV en verde; relleno verde = ya está en el boletín). Pestañas inclinadas
Previa · Alineaciones · Estadísticas · Cuotas (n) · Jugadores (n). Contenido de Previa: campo en
perspectiva con los dos onces a la izquierda; barras comparativas que crecen desde el centro a la
derecha. Boletín como panel lateral derecho que se abre desde el botón.

FORM: Grafismo de retransmisión, nº 1 de la lista ordenada (IMPECCABLE'S PICK; el sorteo asignó el nº 5,
prensa deportiva). seed: none (unavailable). Movimiento firma: barrido de rótulo al entrar (marcador y
paneles), barras que crecen desde el centro, y la cuota pulsada que viaja hasta el botón del boletín.
Todo con prefers-reduced-motion.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
