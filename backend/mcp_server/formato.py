"""Formateo, recorte y saneado de las respuestas del servidor MCP (funciones puras).

Las cifras salen ya escritas a la española y con los mismos criterios que ``core/format.ts``
(cuota ``"1,85"``, probabilidad ``"54,1 %"``, EV ``"+4,2 %"``), para que el LLM las cite tal
cual y no redondee por su cuenta. Las cadenas que vienen de webs externas (equipos, jugadores,
motivos de baja) se recortan y se limpian de caracteres de control antes de devolverse.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

#: Longitud máxima de una cadena externa (nombre de equipo, jugador, liga, motivo de baja).
MAX_TEXTO_EXTERNO = 80

#: Línea fija en toda respuesta con cuotas o picks.
AVISO_JUEGO_RESPONSABLE = (
    "Proyecto educativo: el modelo estima probabilidades y no está calibrado del todo; "
    "no es un consejo de apuesta. Juego responsable, solo +18 (jugarbien.es)."
)

_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

#: Resultado de una pata o apuesta (``grade_single_leg``) en palabras.
RESULTADOS = {
    "hit": "acertada",
    "miss": "fallada",
    "void": "nula",
    "pending": "pendiente",
    "pending_stats": "esperando stats",
    "needs_review": "a revisar",
}

#: Letra de forma (``W``/``D``/``L`` de ``match_preview``) en español: victoria, empate, derrota.
LETRA_FORMA = {"W": "V", "D": "E", "L": "D"}

T = TypeVar("T")


def sanear(texto: Any, maximo: int = MAX_TEXTO_EXTERNO) -> str | None:
    """Cadena externa lista para devolver como dato: sin control, una línea y recortada."""
    if texto is None:
        return None
    limpio = "".join(
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in str(texto)
    )
    limpio = " ".join(limpio.split())
    if len(limpio) > maximo:
        limpio = limpio[: maximo - 1].rstrip() + "…"
    return limpio


def _num(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def fmt_num(valor: Any, decimales: int = 2) -> str | None:
    """``2.5`` → ``"2,50"``; ``None`` se queda en ``None`` (no hay dato)."""
    n = _num(valor)
    if n is None:
        return None
    return f"{n:.{decimales}f}".replace(".", ",")


def fmt_cuota(valor: Any) -> str | None:
    return fmt_num(valor, 2)


def fmt_prob(valor: Any) -> str | None:
    """Probabilidad 0–1 → ``"54,1 %"``."""
    n = _num(valor)
    if n is None:
        return None
    return f"{n * 100:.1f} %".replace(".", ",")


def fmt_ev(valor: Any) -> str | None:
    """EV como fracción (``0.042``) → ``"+4,2 %"``, con signo."""
    n = _num(valor)
    if n is None:
        return None
    signo = "+" if n >= 0 else "−"
    return f"{signo}{abs(n) * 100:.1f} %".replace(".", ",")


def fmt_pct_directo(valor: Any) -> str | None:
    """Porcentaje que ya viene en 0–100 (``rate_pct``) → ``"61,5 %"``."""
    n = _num(valor)
    if n is None:
        return None
    return f"{n:.1f} %".replace(".", ",")


def fmt_euros(valor: Any, signo: bool = False) -> str | None:
    """``"12.5"`` → ``"12,50 €"``; con ``signo``, ``"+12,50 €"`` / ``"−8,00 €"``."""
    n = _num(valor)
    if n is None:
        return None
    txt = f"{abs(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    if n < 0:
        return "−" + txt
    return ("+" + txt) if signo and n > 0 else txt


def fmt_linea(valor: Any) -> str | None:
    """Línea de mercado sin ceros sobrantes: ``"2.50"`` → ``"2,5"``."""
    n = _num(valor)
    if n is None:
        return None
    txt = f"{n:.2f}".rstrip("0").rstrip(".")
    return txt.replace(".", ",")


def fmt_fecha(dt: datetime | str | None) -> str | None:
    """Fecha y hora de Madrid: ``"sáb 15/11 21:00"``."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(MADRID)
    return f"{_DIAS[local.weekday()]} {local:%d/%m %H:%M}"


def fmt_dia(dt: Any) -> str | None:
    """Solo el día: ``"15/11/2026"`` (fechas de picks, que son ``date``)."""
    if dt is None:
        return None
    return f"{dt:%d/%m/%Y}"


def recortar(items: list[T], limite: int) -> tuple[list[T], bool]:
    """``(los primeros `limite`, ¿se ha recortado?)``."""
    limite = max(0, limite)
    return items[:limite], len(items) > limite


def acotar(valor: int, minimo: int, maximo: int) -> int:
    return max(minimo, min(maximo, int(valor)))


def resultado_es(codigo: str | None) -> str:
    return RESULTADOS.get(codigo or "pending", codigo or "pendiente")


def forma_texto(letras: list[str]) -> str:
    """``["W", "D", "L"]`` → ``"VED"`` (del más reciente al más antiguo)."""
    return "".join(LETRA_FORMA.get(x, "?") for x in letras)


def grupo_amplio(market_key: str) -> str:
    """Grupo de mercado para filtrar cuotas: ``1x2``, ``doble``, ``goles``, ``ambos``,
    ``corners``, ``tarjetas``, ``tiros``, ``jugador`` u ``otros``."""
    k = (market_key or "").lower()
    if k == "h2h":
        return "1x2"
    if k == "double_chance":
        return "doble"
    if k == "btts":
        return "ambos"
    if k.startswith("player_"):
        return "jugador"
    if "corners" in k:
        return "corners"
    if "cards" in k:
        return "tarjetas"
    if "shots" in k or "sot" in k:
        return "tiros"
    if "totals" in k or "goals" in k or "scoring" in k:
        return "goles"
    return "otros"


GRUPOS_DE_MERCADO = (
    "1x2", "doble", "goles", "ambos", "corners", "tarjetas", "tiros", "jugador", "otros",
)


def texto_seleccion(
    outcome_name: str | None, outcome_point: Any = None, player_name: str | None = None,
) -> str:
    """``"Over 2,5"``, ``"Lamine Yamal · Over 0,5"``: selección legible y saneada."""
    partes = [sanear(outcome_name) or "?"]
    linea = fmt_linea(outcome_point)
    if linea is not None:
        partes.append(linea)
    sel = " ".join(partes)
    jugador = sanear(player_name)
    return f"{jugador} · {sel}" if jugador else sel


def texto_partido(home: str | None, away: str | None) -> str:
    return f"{sanear(home)} – {sanear(away)}"


def pata_compacta(lg: dict[str, Any]) -> dict[str, Any]:
    """Pata guardada (``pata_a_dict`` / ``grading_snapshot``) → dict compacto para el chat."""
    out: dict[str, Any] = {
        "match_id": lg.get("match_id"),
        "partido": texto_partido(lg.get("home_team"), lg.get("away_team")),
        "fecha": fmt_fecha(lg.get("commence_time")),
        "mercado": lg.get("market_key"),
        "seleccion": texto_seleccion(
            lg.get("outcome_name"), lg.get("outcome_point"), lg.get("player_name"),
        ),
        "cuota": fmt_cuota(lg.get("offered_odds")),
        "p_modelo": fmt_prob(lg.get("model_probability")),
        "ev": fmt_ev(lg.get("model_ev")),
    }
    if lg.get("sin_valor"):
        out["sin_valor"] = True
    if lg.get("experimental"):
        # Prop de jugador (reglas v5): aún sin historial de picks reales.
        out["experimental"] = True
    if lg.get("aviso"):
        out["aviso"] = sanear(lg.get("aviso"), 120)
    if "result" in lg:
        out["resultado"] = resultado_es(lg.get("result"))
    return {k: v for k, v in out.items() if v is not None}


def combinada_compacta(combo: dict[str, Any] | None) -> dict[str, Any] | None:
    if not combo:
        return None
    out: dict[str, Any] = {
        "patas": [pata_compacta(lg) for lg in combo.get("legs") or []],
        "cuota": fmt_cuota(combo.get("combined_odds")),
        "p_conjunta": fmt_prob(combo.get("joint_probability")),
        "ev": fmt_ev(combo.get("ev")),
        "sin_valor": bool(combo.get("sin_valor")),
    }
    if combo.get("mismo_partido"):
        out["mismo_partido"] = True
    if "result" in combo:
        out["resultado"] = resultado_es(combo.get("result"))
    return {k: v for k, v in out.items() if v is not None}


def sin_vacios(d: dict[str, Any]) -> dict[str, Any]:
    """Quita las claves con ``None`` (respuestas más cortas)."""
    return {k: v for k, v in d.items() if v is not None}


def decimal_o_none(valor: Any) -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except ArithmeticError:
        return None


__all__ = [
    "AVISO_JUEGO_RESPONSABLE",
    "GRUPOS_DE_MERCADO",
    "MADRID",
    "MAX_TEXTO_EXTERNO",
    "acotar",
    "combinada_compacta",
    "decimal_o_none",
    "fmt_cuota",
    "fmt_dia",
    "fmt_euros",
    "fmt_ev",
    "fmt_fecha",
    "fmt_linea",
    "fmt_num",
    "fmt_pct_directo",
    "fmt_prob",
    "forma_texto",
    "grupo_amplio",
    "pata_compacta",
    "recortar",
    "resultado_es",
    "sanear",
    "sin_vacios",
    "texto_partido",
    "texto_seleccion",
]
