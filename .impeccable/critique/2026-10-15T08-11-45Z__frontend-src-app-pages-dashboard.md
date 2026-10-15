---
target: dashboard
total_score: 13
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 3
target_identity: "file:C:\\Users\\Fer Moreno\\Desktop\\2 DAM\\Bets AI\\frontend\\src\\app\\pages\\dashboard"
timestamp: 2026-10-15T08-11-45Z
slug: frontend-src-app-pages-dashboard
---
⚠️ Sin navegador: revisión sobre código + API local (Playwright y Chrome no disponibles).
Method: dual-agent (A: design review · B: detector)

## Design Health Score — 13/40 (Poor)

| # | Heurística | Nota | Problema clave |
|---|---|---|---|
| 1 | Estado del sistema | 1 | "Actualizar jornada" se traga los errores del pipeline (dashboard.ts:503); no hay hora del último refresco |
| 2 | Lenguaje del usuario | 2 | Mercados fuera de 4 claves salen como clave cruda (dashboard.ts:463-471); "PostgreSQL" como subtítulo en Mis apuestas |
| 3 | Control y libertad | 1 | Eliminar combinada sin confirmación ni deshacer (mis-apuestas.ts:102-105); error de cuotas sustituye la página sin reintentar |
| 4 | Consistencia | 1 | Tres lenguajes de color (brand-*, slate/emerald, teal); cuatro verbos de recarga |
| 5 | Prevención de errores | 1 | Botón principal lanza scraping pesado sin confirmación (dashboard.html:13) |
| 6 | Reconocer, no recordar | 1 | Filtros se reinician al cambiar de partido; Selección no depende del Mercado |
| 7 | Flexibilidad | 1 | Sin vista "mejores líneas de hoy"; hay que abrir 103 partidos uno a uno |
| 8 | Estética minimalista | 2 | Limpio, pero ~200px de cabecera antes del primer dato |
| 9 | Recuperación de errores | 1 | "No hay cuotas que coincidan con estos filtros" cuando el partido no tiene cuotas (dashboard.html:125) |
| 10 | Ayuda | 2 | Transparencia explica bien; Mis apuestas manda a un "Simulador" que ya no existe (mis-apuestas.html:77) |

## Veredicto de especificidad
Genérico: el dashboard enrutado es un visor partido → cuotas. dashboard.ts (730 líneas) tiene KPIs, EV, confianza, presets de mercado (jugadores, mitades), simulador de combinada con aviso de partido duplicado y guardado, pero `gradedRows` (L142) nunca se rellena y la plantilla no usa `displayRows`, `kpis` ni `parlayLegs`.
Detector: dashboard limpio; 4 hallazgos en toda la app: 3 `broken-image` en estadisticas.html (falsos positivos, usan [src]) y 1 `ai-color-palette` en calibration.html (página sin ruta).

## Problemas prioritarios
- [P0] El dashboard no hace el trabajo del producto: EV/P modelo/confianza y combinadas no se ven. Fix: vista por defecto "Líneas con valor" alimentada por /dashboard/graded-odds con displayRows + panel de combinada. → /impeccable shape
- [P1] El texto culpa a los filtros cuando faltan datos (dashboard.html:124-125). → /impeccable clarify
- [P1] La acción principal es un scraping pesado y silencioso (dashboard.html:13, ts:495-503). → /impeccable harden
- [P1] Un error de cuotas tapa toda la página sin reintentar (ts:455, html:37-41). → /impeccable harden
- [P2] Los mercados favoritos (tiros a puerta de jugador, goles 2ª parte) no se pueden filtrar y salen con clave cruda. → /impeccable clarify

## Personas
Alex: 103 filas sin "top EV hoy"; clics ignorados mientras carga; filtros numéricos solo al perder foco; tope 4.00 fijo; nada persiste.
Sam: partido seleccionado solo por color (sin aria-pressed); sin aria-live en el panel de cuotas; spinners sin role=status; cuotas en divs, no tabla; sin focus-visible.

## Inventario de páginas
login, transparencia, mis-apuestas: mantener. dashboard: reconstruir plantilla sobre la lógica existente. estadisticas: mantener y añadir datos por mitad/tiros. match-detail: enrutar. matches y value-bets: fusionar en el dashboard. recommendations y analisis: fusionar o borrar. calibration: sin ruta hasta fase 3.

## Observaciones menores
Pie y logo en inglés ("Intelligent market analysis"), "MVP" visible; estadisticas menciona Sofascore; KPIs de Mis apuestas apretados en 5 columnas.
