# Bets AI

Sistema automatizado de detección de apuestas de valor (value bets) en fútbol europeo, con perfiles de usuario personalizables y explicaciones generadas por IA.

## Idea de negocio

Bets AI no es un bot que "te dice qué apostar"; es un **asesor financiero deportivo** que:

1. **Captura cuotas reales** de Winamax, Pinnacle y otros bookmakers europeos vía The Odds API.
2. **Cruza esas cuotas con estadísticas avanzadas** (xG, forma, head-to-head) de FBref y Understat.
3. **Calcula el valor esperado (EV)** de cada apuesta de forma matemática (criterio de Kelly).
4. **Adapta las recomendaciones al perfil del usuario** (conservador, equilibrado, arriesgado, cazador de premios).
5. **Sugiere combinadas inteligentes** (parlays) entre apuestas con valor positivo.
6. **Explica cada recomendación en lenguaje natural** mediante un LLM (Gemini Flash).

Cobertura: **Las 5 grandes ligas europeas** (LaLiga, Premier, Bundesliga, Serie A, Ligue 1) **+ Champions, Europa y Conference League**.

## Diferencia respecto a un LLM puro

| Capacidad | LLM solo (ChatGPT) | Bets AI |
|---|---|---|
| Cuotas reales en tiempo real | No | Sí, vía API |
| EV calculado matemáticamente | No | Sí, fórmula determinista |
| Player props (tiros a puerta, etc.) | No | Sí, scraper Winamax |
| Perfil de usuario y bankroll | No | Sí, persistente en BD |
| Historial de apuestas trackeado | No | Sí |
| Reproducibilidad | No | Sí |
| Programado / automatizado | No | Sí, GitHub Actions |
| Explicación en lenguaje natural | Sí | Sí (LLM como capa final) |

## Stack técnico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Base de datos | PostgreSQL 16 (Docker) |
| ORM | SQLAlchemy 2 |
| Scraping | Requests + BeautifulSoup + Playwright (para Winamax) |
| Frontend | Angular 21 |
| ML / Modelo | scipy / pandas (Poisson-Dixon-Coles) |
| LLM | Google Gemini 1.5 Flash (free tier) |
| Automatización | APScheduler + GitHub Actions |
| Hosting (futuro) | Vercel (front) + Render/Fly.io (back) + Supabase (BD) |

**Coste de operación: 0 €/mes** en plan free.

## Modelo de valor (EV)

Para cada apuesta el sistema calcula:

```
prob_implícita_bookie = 1 / cuota
prob_real             = estimada vía Pinnacle/1xBet (sin vig) o modelo Poisson sobre xG
EV                    = (cuota * prob_real) - 1
stake_recomendado     = bankroll * Kelly_fraction * (EV / (cuota - 1))
```

Hay valor cuando `EV > 0`. El stake se ajusta al perfil del usuario.

### Perfiles disponibles

| Perfil | Cuotas | Prob. real mín | EV mín | Stake | Notas |
|---|---|---|---|---|---|
| Conservador | 1.30-1.70 | ≥ 70% | +3% | Kelly/4 | Combinadas dobles, 5€/apuesta |
| Equilibrado | 1.50-2.50 | ≥ 55% | +5% | Kelly/2 | Mix individual + combinadas |
| Arriesgado | 2.00-5.00 | sin mín. | +7% | Kelly | Singles |
| Cazador de premios | 3.00+ | sin mín. | +10% | Stake fijo bajo | Modo lotería |

## Mercados cubiertos

### Vía The Odds API (Capa 1)
- `h2h` — 1X2 ganador
- `double_chance` — doble oportunidad (1X, X2, 12)
- `totals` — Over/Under goles

### Vía scraper Winamax (Capa 2)
- Tiros a puerta de jugador

### Pendientes (capas futuras)
- BTTS (Both Teams To Score)
- Spreads (hándicap asiático)
- Córners y tarjetas
- Más player props

## Estrategia de actualización

Para no agotar el plan free de The Odds API (500 créditos/mes):

| Día | Hora | Acción | Coste |
|---|---|---|---|
| Jueves | AM | Snapshot completo de las 8 ligas | 24 cred |
| Sábado | AM | Refresh ligas con partido sábado | ~15 cred |
| Domingo | AM | Refresh ligas con partido domingo | ~15 cred |
| Mar/Mié | AM | Champions/Europa/Conference | ~10 cred |

**Total estimado: 250-310 créditos/mes**, sobra para pruebas.

## Plan por fases

| Fase | Objetivo | Estado |
|---|---|---|
| **0** | Setup: estructura, deps, .env, Docker, README | En curso |
| **1** | Scraper The Odds API → guarda en BD | Pendiente |
| **2** | Scraper Winamax (tiros a puerta) → BD | Pendiente |
| **3** | Stats FBref/Understat + cálculo de prob. real y EV | Pendiente |
| **4** | Backend FastAPI + endpoints | Pendiente |
| **5** | Frontend Angular: dashboard, perfiles, recomendaciones | Pendiente |
| **6** | Automatización con GitHub Actions | Pendiente |
| **7** | Capa LLM (Gemini) para explicaciones en lenguaje natural | Pendiente |

## Setup local

### Requisitos
- Python 3.12+
- Node.js 22+ y Angular CLI 21+
- Docker Desktop (para PostgreSQL)
- Una API key de [The Odds API](https://the-odds-api.com/#get-access) (free)

### Instalación

```powershell
# 1. Clonar el repo
git clone <repo_url>
cd "Bets AI"

# 2. Crear entorno virtual e instalar deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Configurar variables de entorno
copy .env.example .env
# (editar .env y poner tu ODDS_API_KEY real)

# 4. Levantar PostgreSQL
docker compose up -d

# 5. Probar el scraper de cuotas
python -m backend.scrapers.odds_api
```

## Estructura del proyecto

```
bets-ai/
├── backend/
│   ├── app/              # Aplicación FastAPI (config, BD, main)
│   ├── scrapers/         # Scrapers (The Odds API, Winamax, FBref, ...)
│   ├── models/           # Modelos SQLAlchemy y lógica de cálculo (EV, Kelly)
│   ├── services/         # Servicios de negocio (recomendador, perfiles)
│   └── tests/            # Tests unitarios y de integración
├── frontend/             # Aplicación Angular (dashboard)
├── data/
│   ├── raw/              # JSONs crudos del scraping
│   └── processed/        # Datos transformados
├── docs/                 # Documentación adicional
├── docker-compose.yml    # PostgreSQL en contenedor
├── requirements.txt      # Dependencias Python
└── .env.example          # Plantilla de variables de entorno
```

## Aviso legal

Este proyecto es **educativo y de portfolio**. No constituye asesoramiento financiero. El juego puede ser adictivo; apuesta solo con dinero que estés dispuesto a perder.
