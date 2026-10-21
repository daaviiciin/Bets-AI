"""Fotos de los jugadores: se enlazan al CDN de imágenes de Flashscore, no se descargan.

En ``players.photo_path`` solo se guarda el fichero que trae ``epmsse``
(``"zJsYDqgl-URqd4A5l.png"``); aquí se monta la URL completa para las respuestas de la API.
"""

from __future__ import annotations

from backend.services.team_crests import flashscore_logo_url


def player_photo_url(path: str | None) -> str | None:
    """``"zJsYDqgl-URqd4A5l.png"`` → URL del CDN; vacío o ruta rara → ``None``."""
    return flashscore_logo_url(path)


__all__ = ["player_photo_url"]
