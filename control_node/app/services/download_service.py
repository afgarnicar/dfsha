"""Servicio de descarga de archivos (GET).

Reconstruye un archivo a partir de sus bloques distribuidos:

1. Valida permiso de lectura, que el archivo esté AVAILABLE y que nadie tenga
   un lock de escritura sobre él.
2. Consulta en PostgreSQL los bloques de la versión actual y sus réplicas.
3. Reserva espacio temporal (tamaño + margen) para no sobreasignar el disco.
4. Descarga los bloques en paralelo (PRIMARY primero, BACKUP si falla),
   validando el SHA-256 de cada uno, y los descifra uno por uno.
5. Entrega una ``DownloadSession`` que envía las partes en orden y, al
   terminar (bien o mal), borra los temporales y libera la reserva.

Los bloques se descifran ANTES de empezar a responder: si hay un error (por
ejemplo, una clave de cifrado distinta), el cliente recibe un error claro en
lugar de un archivo cortado a la mitad.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.block_repository import BlockRepository
from app.security.permissions import Action
from app.services.block_assembler import (
    BlockAssembler,
    FileDecryptionError,
    FileIncompleteError,
    check_complete,
    encrypted_size,
)
from app.services.block_fetcher import BlockUnavailableError
from app.services.file_access_service import FileAccessService
from app.services.lock_service import LockService
from app.services.temp_storage_service import TempWorkspace, TemporaryStorageService

# Tamaño de cada trozo que se envía al cliente.
STREAM_CHUNK_BYTES = 1024 * 1024


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
                    while chunk := part.read(STREAM_CHUNK_BYTES):
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
        lock_timeout_seconds: int,
    ) -> None:
        self._access = FileAccessService(session)
        self._blocks = BlockRepository(session)
        self._locks = LockService(session, lock_timeout_seconds)
        self._assembler = BlockAssembler(temp_storage, max_parallel_downloads)

    def prepare(self, user: User, path: str) -> DownloadSession:
        """Descarga, valida y descifra todos los bloques de un archivo.

        Si algo falla, limpia lo que alcanzó a crear antes de lanzar el error.
        """
        located = self._access.locate(user, path)
        file = located.file
        self._access.require_file_permission(user, file, Action.READ)
        self._locks.ensure_not_locked(file.id)

        blocks = self._blocks.list_for_file_version(file.id, file.current_version)
        check_complete(file, blocks)

        workspace = self._assembler.reserve(BlockAssembler.bytes_needed(blocks))
        try:
            self._assembler.assemble(blocks, workspace)
        except BaseException:
            workspace.release()
            raise

        return DownloadSession(
            file_name=file.name,
            size_bytes=file.size_bytes,
            workspace=workspace,
            part_count=len(blocks),
        )


__all__ = [
    "BlockUnavailableError",
    "DownloadService",
    "DownloadSession",
    "FileDecryptionError",
    "FileIncompleteError",
    "STREAM_CHUNK_BYTES",
    "encrypted_size",
]
