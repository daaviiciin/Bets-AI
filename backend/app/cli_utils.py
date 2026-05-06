"""Utilidades para scripts ejecutados en CLI (Windows-friendly)."""

from __future__ import annotations

import io
import sys


def force_utf8_stdio() -> None:
    """Reconfigura stdout/stderr a UTF-8 una sola vez.

    En Windows, la consola por defecto usa CP-1252 y rompe acentos. Llamar
    a esta función como primera línea del ``main()`` evita corrupción del
    output. Es idempotente: si ya está aplicado o no estamos en Windows,
    no hace nada.
    """
    if sys.platform != "win32":
        return
    if getattr(sys.stdout, "encoding", "").lower() == "utf-8":
        return
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace",
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace",
        )
    except (AttributeError, ValueError):
        # buffer ya cerrado o stream no estándar; ignoramos.
        pass
