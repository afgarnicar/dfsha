"""Utilidades de checksum SHA-256 para los bloques.

El DataNode calcula el SHA-256 sobre los bytes tal cual le llegan. No sabe ni le
importa si vienen cifrados: para él un bloque es un contenido opaco.
"""

import hashlib
import re
from pathlib import Path

# Tamaño de cada lectura al recorrer un archivo. Leer por partes evita cargar
# un bloque completo (hasta 64 MB) en memoria.
CHUNK_SIZE_BYTES = 1024 * 1024

# Un SHA-256 en hexadecimal tiene exactamente 64 caracteres.
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def is_valid_sha256_hex(value: str) -> bool:
    """Indica si el texto tiene la forma de un SHA-256 en hexadecimal."""
    return bool(_SHA256_HEX_PATTERN.match(value))


def normalize_sha256_hex(value: str) -> str:
    """Pasa el checksum a minúsculas para compararlo sin importar mayúsculas."""
    return value.strip().lower()


def file_sha256(path: Path) -> str:
    """Calcula el SHA-256 de un archivo leyéndolo por partes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
