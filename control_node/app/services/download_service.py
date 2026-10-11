"""Servicio de descarga de archivos (GET).

Reconstruye un archivo a partir de sus bloques distribuidos:

1. Valida permiso de lectura y que el archivo esté AVAILABLE.
2. Consulta en PostgreSQL los bloques de la versión actual y sus réplicas.
3. Reserva espacio temporal (tamaño + margen) para no sobreasignar el disco.
4. Descarga los bloques en paralelo (PRIMARY primero, BACKUP si falla),
   validando el SHA-256 de cada uno.
5. Descifra los bloques uno por uno, en orden, en la carpeta temporal.
6. Entrega una ``DownloadSession`` que envía las partes en orden y, al
   terminar (bien o mal), borra los temporales y libera la reserva.

Los bloques se descifran ANTES de empezar a responder: si hay un error (por
ejemplo, una clave de cifrado distinta), el cliente recibe un error claro en
lugar de un archivo cortado a la mitad.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from app.models.filesystem import File
from app.models.storage import Block
from app.models.user import User
from app.repositories.block_repository import BlockRepository
from app.security.encryption import BlockCipher
from app.security.permissions import Action
from app.services.block_fetcher import (
    BlockFetcher,
    BlockFetchPlan,
    BlockUnavailableError,
    ReplicaSource,
)
from app.services.file_access_service import FileAccessService
from app.services.temp_storage_service import TempWorkspace, TemporaryStorageService
from shared.enums import NodeStatus, ReplicaRole

logger = logging.getLogger(__name__)

# Tamaño de cada trozo que se envía al cliente.
_STREAM_CHUNK_BYTES = 1024 * 1024


class FileIncompleteError(Exception):
    """Los metadatos del archivo no tienen todos sus bloques o réplicas."""


class FileDecryptionError(Exception):
    """Un bloque no se pudo descifrar (clave distinta o contenido alterado)."""


def encrypted_size(plaintext_size: int) -> int:
    """Tamaño exacto que ocupa un bloque cifrado con Fernet.

    Fernet arma un token de 57 bytes fijos + el contenido con relleno AES a
    múltiplos de 16, y lo entrega en base64 (4 caracteres por cada 3 bytes).
    Se usa para reservar el espacio temporal justo antes de descargar.
    """
    raw = 57 + (plaintext_size // 16 + 1) * 16
    return 4 * ((raw + 2) // 3)


@dataclass
class DownloadSession:
    """Archivo listo para enviarse: partes descifradas en una carpeta temporal."""

    file_name: str
    size_bytes: int
    workspace: TempWorkspace
    part_count: int

    def iter_content(self) -> Iterator[bytes]:
        """Envía las partes en orden y limpia los temporales al terminar."""
        try:
            for index in range(self.part_count):
                with self.workspace.part_path(index).open("rb") as part:
                    while chunk := part.read(_STREAM_CHUNK_BYTES):
                        yield chunk
        finally:
            self.close()

    def close(self) -> None:
        """Borra los temporales y libera la reserva (se puede llamar varias veces)."""
        self.workspace.release()


class DownloadService:
    """Coordina la reconstrucción de un archivo desde sus bloques."""

    def __init__(
        self,
        session: Session,
        temp_storage: TemporaryStorageService,
        max_parallel_downloads: int,
    ) -> None:
        self._access = FileAccessService(session)
        self._blocks = BlockRepository(session)
        self._temp_storage = temp_storage
        self._fetcher = BlockFetcher(max_parallel_downloads)
        self._cipher = BlockCipher()

    def prepare(self, user: User, path: str) -> DownloadSession:
        """Descarga, valida y descifra todos los bloques de un archivo.

        Si algo falla, limpia lo que alcanzó a crear antes de lanzar el error.
        """
        located = self._access.locate(user, path)
        file = located.file
        self._access.require_file_permission(user, file, Action.READ)

        blocks = self._blocks.list_for_file_version(file.id, file.current_version)
        self._check_complete(file, blocks)

        # Durante la descarga conviven los bloques cifrados y, mientras se
        # descifra, una parte en claro: se reserva para el peor momento.
        encrypted_total = sum(encrypted_size(b.used_size_bytes) for b in blocks)
        largest_plain = max((b.used_size_bytes for b in blocks), default=0)
        workspace = self._temp_storage.reserve(encrypted_total + largest_plain)

        try:
            plans = [self._plan_for(block, workspace) for block in blocks]
            self._fetcher.fetch_all(plans)
            self._decrypt_parts(blocks, workspace)
        except BaseException:
            workspace.release()
            raise

        return DownloadSession(
            file_name=file.name,
            size_bytes=file.size_bytes,
            workspace=workspace,
            part_count=len(blocks),
        )

    @staticmethod
    def _check_complete(file: File, blocks: list[Block]) -> None:
        """Verifica que estén todos los bloques, en orden y con al menos una réplica."""
        indexes = [block.block_index for block in blocks]
        if indexes != list(range(len(blocks))):
            raise FileIncompleteError(f"faltan bloques del archivo {file.name!r}")
        if sum(block.used_size_bytes for block in blocks) != file.size_bytes:
            raise FileIncompleteError(f"el tamaño de los bloques de {file.name!r} no cuadra")
        for block in blocks:
            if not block.locations:
                raise FileIncompleteError(
                    f"el bloque {block.block_index + 1} de {file.name!r} no tiene réplicas"
                )

    @staticmethod
    def _plan_for(block: Block, workspace: TempWorkspace) -> BlockFetchPlan:
        """Arma el plan de descarga de un bloque: PRIMARY primero, BACKUP después.

        Las réplicas en nodos OFFLINE se dejan de últimas: igual se intentan,
        por si el nodo ya volvió y el monitor todavía no lo ha visto.
        """
        def order(location) -> tuple[int, int]:
            offline = location.datanode.status == NodeStatus.OFFLINE
            backup = location.role != ReplicaRole.PRIMARY
            return (int(offline), int(backup))

        replicas = [
            ReplicaSource(
                node_name=location.datanode.name,
                host=location.datanode.host,
                port=location.datanode.port,
                role=location.role.value,
            )
            for location in sorted(block.locations, key=order)
        ]
        return BlockFetchPlan(
            block_index=block.block_index,
            storage_key=block.storage_key,
            checksum=block.checksum,
            target_path=workspace.part_path(block.block_index, "enc"),
            replicas=replicas,
        )

    def _decrypt_parts(self, blocks: list[Block], workspace: TempWorkspace) -> None:
        """Descifra cada bloque descargado, de a uno para limitar la memoria usada."""
        for block in blocks:
            encrypted_path = workspace.part_path(block.block_index, "enc")
            try:
                plaintext = self._cipher.decrypt(encrypted_path.read_bytes())
            except InvalidToken as error:
                raise FileDecryptionError(
                    f"no se pudo descifrar el bloque {block.block_index + 1}"
                ) from error
            if len(plaintext) != block.used_size_bytes:
                raise FileDecryptionError(
                    f"el bloque {block.block_index + 1} no tiene el tamaño esperado"
                )
            workspace.part_path(block.block_index).write_bytes(plaintext)
            encrypted_path.unlink()
            del plaintext


__all__ = [
    "BlockUnavailableError",
    "DownloadService",
    "DownloadSession",
    "FileDecryptionError",
    "FileIncompleteError",
    "encrypted_size",
]
