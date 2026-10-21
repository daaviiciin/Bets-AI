"""CLI: cambia la contraseña de un usuario de la app (C-2).

Uso::

    python -m backend.tools.set_password admin
    python -m backend.tools.set_password otro_usuario --crear

Pide la contraseña dos veces sin eco (``getpass``) y guarda el hash PBKDF2 de
``services/auth_password.py``. Las sesiones abiertas siguen valiendo hasta que caduquen
(el JWT no depende de la contraseña); para cerrarlas todas, cambia también ``JWT_SECRET``.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import AppUser
from backend.services.auth_password import LONGITUD_MINIMA_PASSWORD, hash_password

LONGITUD_MAXIMA_PASSWORD = 256  # la misma que acepta POST /auth/login


def validar_password_nueva(primera: str, segunda: str) -> str | None:
    """Devuelve el motivo del rechazo, o ``None`` si la contraseña vale."""
    if primera != segunda:
        return "Las contraseñas no coinciden."
    if len(primera) < LONGITUD_MINIMA_PASSWORD:
        return f"La contraseña debe tener al menos {LONGITUD_MINIMA_PASSWORD} caracteres."
    if len(primera) > LONGITUD_MAXIMA_PASSWORD:
        return f"La contraseña no puede pasar de {LONGITUD_MAXIMA_PASSWORD} caracteres."
    if primera.strip() != primera:
        return "La contraseña no puede empezar ni acabar con espacios."
    if len(set(primera)) < 4:
        return "La contraseña es demasiado repetitiva."
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cambia la contraseña de un usuario.")
    parser.add_argument("usuario", help="Nombre de usuario (p. ej. admin).")
    parser.add_argument("--crear", action="store_true", help="Crea el usuario si no existe.")
    args = parser.parse_args(argv)
    force_utf8_stdio()

    usuario = args.usuario.strip()
    if not usuario or len(usuario) > 64:
        print("Nombre de usuario no válido (1-64 caracteres).", file=sys.stderr)
        return 2

    with get_session_factory()() as session:
        user = session.scalar(select(AppUser).where(AppUser.username == usuario))
        if user is None and not args.crear:
            print(f"No existe el usuario '{usuario}'. Usa --crear para darlo de alta.",
                  file=sys.stderr)
            return 1

        try:
            primera = getpass.getpass(f"Nueva contraseña para '{usuario}': ")
            segunda = getpass.getpass("Repítela: ")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelado.", file=sys.stderr)
            return 130
        error = validar_password_nueva(primera, segunda)
        if error:
            print(error, file=sys.stderr)
            return 1

        if user is None:
            user = AppUser(username=usuario, password_hash=hash_password(primera))
            session.add(user)
        else:
            user.password_hash = hash_password(primera)
        session.commit()

    print(f"Contraseña de '{usuario}' actualizada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
