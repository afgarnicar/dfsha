"""Almacenamiento físico de bloques en el disco del DataNode.

Cada bloque se guarda como un archivo plano en ``{storage_root}/{block_id}``.
El ``block_id`` lo genera el ControlNode y aquí no se interpreta: solo se valida
que sea seguro usarlo como nombre de archivo.

Escritura segura: el contenido se escribe primero en un archivo temporal dentro
de la misma carpeta y, solo cuando está completo y su checksum coincide, se
renombra al nombre final. El renombrado es atómico, así que nunca queda un
bloque a medio escribir con el nombre definitivo.
"""

import hashlib
import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.storage.checksum import CHUNK_SIZE_BYTES, normalize_sha256_hex

# Formato permitido para un block_id: letras, números, punto, guion y guion
# bajo, empezando por letra o número, máximo 128 caracteres. Esto evita que un
# id como "../../etc/passwd" se salga de la carpeta de almacenamiento, y que
# choque con los archivos temporales (que empiezan por punto).
_BLOCK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Prefijo y sufijo de los archivos temporales de escritura.
_TEMP_PREFIX = "."
_TEMP_SUFFIX = ".tmp"


class InvalidBlockIdError(ValueError):
    """El block_id no tiene un formato válido para usarse como nombre de archivo."""


class BlockNotFoundError(LookupError):
    """El bloque pedido no existe en este DataNode."""


class ChecksumMismatchError(ValueError):
    """El checksum recibido no coincide con el contenido."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"checksum esperado {expected}, calculado {actual}")
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class StoredBlock:
    """Resultado de guardar un bloque."""

    block_id: str
    size_bytes: int
    checksum: str


def validate_block_id(block_id: str) -> str:
    """Devuelve el block_id si es válido; si no, lanza InvalidBlockIdError."""
    if not _BLOCK_ID_PATTERN.match(block_id):
        raise InvalidBlockIdError(block_id)
    return block_id


class BlockStore:
    """Guarda, lee y borra bloques como archivos planos en una carpeta."""

    def __init__(self, storage_root: str) -> None:
        self._root = Path(storage_root)

    def ensure_root(self) -> None:
        """Crea la carpeta de almacenamiento si no existe."""
        self._root.mkdir(parents=True, exist_ok=True)

    def remove_leftover_temp_files(self) -> int:
        """Borra archivos temporales que quedaron de escrituras interrumpidas.

        Se llama al arrancar: si el contenedor se apagó a mitad de una escritura,
        el temporal queda huérfano. Devuelve cuántos se borraron.
        """
        removed = 0
        for path in self._root.glob(f"{_TEMP_PREFIX}*{_TEMP_SUFFIX}"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def path_for(self, block_id: str) -> Path:
        """Ruta en disco del bloque (valida el id antes)."""
        return self._root / validate_block_id(block_id)

    def save(self, block_id: str, source: BinaryIO, expected_checksum: str) -> StoredBlock:
        """Guarda un bloque verificando su SHA-256.

        Lee ``source`` por partes, lo escribe en un temporal y calcula el
        SHA-256 al mismo tiempo. Si el checksum no coincide, borra el temporal
        y lanza ChecksumMismatchError sin tocar el bloque existente (si lo hay).
        Si ya existía un bloque con ese id, se reemplaza: la identidad de los
        bloques la controla el ControlNode, así que un reintento con el mismo id
        debe dejar el mismo resultado.
        """
        final_path = self.path_for(block_id)
        temp_path = self._root / f"{_TEMP_PREFIX}{block_id}.{uuid.uuid4().hex}{_TEMP_SUFFIX}"
        expected = normalize_sha256_hex(expected_checksum)
        digest = hashlib.sha256()
        size = 0

        try:
            with temp_path.open("wb") as target:
                while chunk := source.read(CHUNK_SIZE_BYTES):
                    digest.update(chunk)
                    target.write(chunk)
                    size += len(chunk)
                # Asegura que los bytes estén en disco antes de renombrar.
                target.flush()
                os.fsync(target.fileno())

            actual = digest.hexdigest()
            if actual != expected:
                raise ChecksumMismatchError(expected, actual)

            os.replace(temp_path, final_path)
        finally:
            # Si algo falló antes del renombrado, el temporal sigue ahí y se borra.
            temp_path.unlink(missing_ok=True)

        return StoredBlock(block_id=block_id, size_bytes=size, checksum=actual)

    def size_of(self, block_id: str) -> int:
        """Tamaño en bytes de un bloque existente."""
        path = self.path_for(block_id)
        if not path.is_file():
            raise BlockNotFoundError(block_id)
        return path.stat().st_size

    def iter_chunks(self, block_id: str) -> Iterator[bytes]:
        """Recorre el contenido de un bloque por partes (para enviarlo en streaming).

        El archivo se abre aquí mismo, antes de devolver el iterador, para que un
        bloque inexistente falle de inmediato y no a mitad de la respuesta.
        """
        path = self.path_for(block_id)
        try:
            handle = path.open("rb")
        except FileNotFoundError as error:
            raise BlockNotFoundError(block_id) from error

        def _generate() -> Iterator[bytes]:
            with handle:
                while chunk := handle.read(CHUNK_SIZE_BYTES):
                    yield chunk

        return _generate()

    def delete(self, block_id: str) -> bool:
        """Borra un bloque. Devuelve True si existía y False si ya no estaba."""
        path = self.path_for(block_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
