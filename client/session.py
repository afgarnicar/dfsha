"""Sesión local del cliente CLI.

Guarda en el disco del usuario el token obtenido al iniciar sesión y la URL del
ControlNode, para que los comandos siguientes (ls, put, get, ...) no tengan que
pedir las credenciales de nuevo.

Los datos se guardan en ~/.dfsha/session.json con permisos restringidos, porque
el token es una credencial.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Carpeta y archivo donde se guarda la sesión del usuario.
_SESSION_DIR = Path.home() / ".dfsha"
_SESSION_FILE = _SESSION_DIR / "session.json"

# URL por defecto del ControlNode (en despliegue se cambia con --base-url).
DEFAULT_BASE_URL = "https://localhost"


@dataclass
class Session:
    """Datos de la sesión actual del cliente."""

    base_url: str = DEFAULT_BASE_URL
    token: str | None = None


def load_session() -> Session:
    """Carga la sesión guardada, o devuelve una por defecto si no existe."""
    if not _SESSION_FILE.exists():
        return Session()
    try:
        data = json.loads(_SESSION_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return Session()
    return Session(
        base_url=data.get("base_url", DEFAULT_BASE_URL),
        token=data.get("token"),
    )


def save_session(session: Session) -> None:
    """Guarda la sesión en el disco con permisos restringidos."""
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(
        json.dumps({"base_url": session.base_url, "token": session.token})
    )
    # Solo el dueño puede leer o escribir el archivo (contiene el token).
    os.chmod(_SESSION_FILE, 0o600)


def clear_session() -> None:
    """Borra la sesión guardada (cierre de sesión local)."""
    _SESSION_FILE.unlink(missing_ok=True)
