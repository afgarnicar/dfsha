"""Configuración centralizada del DataNode.

Toda la configuración se lee desde variables de entorno en un único lugar. El
resto del código debe importar ``get_settings()`` en lugar de llamar a
``os.getenv`` de forma dispersa, para mantener una sola fuente de verdad.
"""

import os
from dataclasses import dataclass
from functools import lru_cache


def _require(name: str) -> str:
    """Obtiene una variable de entorno obligatoria o falla con un mensaje claro."""
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(
            f"Falta la variable de entorno obligatoria: {name}. "
            "Revise su archivo .env (vea .env.example) o el docker-compose.yml."
        )
    return value


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del DataNode cargada desde el entorno."""

    # Identificador del nodo (DN1, DN2, ...). Debe coincidir con el nombre
    # registrado en el ControlNode a través de la variable DATANODES.
    node_id: str

    # Carpeta donde el nodo guarda los bloques. Debe ser un volumen persistente.
    storage_root: str


@lru_cache
def get_settings() -> Settings:
    """Construye y memoiza la configuración leída del entorno."""
    return Settings(
        node_id=_require("NODE_ID"),
        storage_root=os.getenv("STORAGE_ROOT", "/data/dfsha"),
    )
