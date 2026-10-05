"""Configuración centralizada del ControlNode.

Toda la configuración se lee desde variables de entorno en un único lugar. El
resto del código debe importar la instancia ``settings`` en lugar de llamar a
``os.getenv`` de forma dispersa, para mantener una sola fuente de verdad.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from shared.constants import BYTES_PER_MEGABYTE


def _require(name: str) -> str:
    """Obtiene una variable de entorno obligatoria o falla con un mensaje claro."""
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(
            f"Falta la variable de entorno obligatoria: {name}. "
            "Revise su archivo .env (vea .env.example)."
        )
    return value


def _get_int(name: str, default: int) -> int:
    """Lee una variable de entorno entera, usando un valor por defecto si no existe."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise RuntimeError(
            f"La variable de entorno {name} debe ser un número entero, "
            f"se recibió: {raw!r}."
        ) from error


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del ControlNode cargada desde el entorno."""

    # Conexión a la base de datos de metadatos.
    database_url: str

    # Seguridad de usuarios (JWT) y comunicación interna entre nodos (API key).
    jwt_secret: str
    jwt_expiration_seconds: int
    internal_api_key: str

    # Parámetros del sistema de archivos distribuido.
    block_size_mb: int
    replication_factor: int
    max_retries: int
    max_parallel_downloads: int
    lock_timeout_seconds: int
    health_check_interval_seconds: int
    datanode_failure_threshold: int
    temp_space_margin_percent: int
    storage_root: str

    @property
    def block_size_bytes(self) -> int:
        """Tamaño de bloque expresado en bytes."""
        return self.block_size_mb * BYTES_PER_MEGABYTE


@lru_cache
def get_settings() -> Settings:
    """Construye y memoiza la configuración leída del entorno.

    Se usa una caché para que la configuración se lea una sola vez por proceso.
    """
    return Settings(
        database_url=_require("DATABASE_URL"),
        jwt_secret=_require("JWT_SECRET"),
        jwt_expiration_seconds=_get_int("JWT_EXPIRATION", 3600),
        internal_api_key=_require("INTERNAL_API_KEY"),
        block_size_mb=_get_int("BLOCK_SIZE_MB", 64),
        replication_factor=_get_int("REPLICATION_FACTOR", 2),
        max_retries=_get_int("MAX_RETRIES", 3),
        max_parallel_downloads=_get_int("MAX_PARALLEL_DOWNLOADS", 8),
        lock_timeout_seconds=_get_int("LOCK_TIMEOUT_SECONDS", 30),
        health_check_interval_seconds=_get_int("HEALTH_CHECK_INTERVAL_SECONDS", 10),
        datanode_failure_threshold=_get_int("DATANODE_FAILURE_THRESHOLD", 3),
        temp_space_margin_percent=_get_int("TEMP_SPACE_MARGIN_PERCENT", 10),
        storage_root=os.getenv("STORAGE_ROOT", "/data/dfsha"),
    )
