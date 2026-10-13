"""Servicio de subida de archivos (PUT), con sobrescritura opcional.

Flujo de un PUT normal:

1. Valida el permiso de escritura en el directorio destino.
2. Rechaza si el archivo ya existe (409) o si no hay 2 DataNodes elegibles.
3. Crea el archivo en estado UPLOADING.
4. Lee el contenido por bloques de 64 MB (sin cargarlo entero en memoria), cifra
   cada bloque y lo guarda en PRIMARY y BACKUP con Round Robin. Si una réplica
   falla, se prueba en otro DataNode elegible (``BlockWriter``).
5. Si todos los bloques quedan con factor de replicación 2, el archivo pasa a
   AVAILABLE. Si algo falla, se descarta: se borran sus bloques y se libera el
   nombre.

Flujo de un PUT con ``overwrite`` sobre un archivo que ya existe:

1. Exige permiso de escritura sobre el archivo y toma su WRITE LOCK (si otra
   sesión lo tiene, responde 423). Mientras dura, nadie lo lee ni lo modifica.
2. Guarda el contenido nuevo como una versión nueva, igual que el write.
3. Solo cuando TODOS los bloques nuevos están guardados, activa la versión nueva
   y borra la vieja. Si algo falla, la versión vieja sigue intacta y disponible.

El contenido en claro nunca llega a los DataNodes: se cifra antes de enviarlo.
"""

import logging
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.distribution.placement_service import NotEnoughEligibleNodesError
from app.models.filesystem import Directory, File
from app.models.user import User
from app.repositories.acl_repository import RESOURCE_DIRECTORY
from app.repositories.block_repository import BlockRepository
from app.repositories.file_repository import FileRepository
from app.security.permissions import Action
from app.services.block_client import BlockSendError
from app.services.block_writer import BlockWriter, StoredVersion
from app.services.delete_service import DeleteService
from app.services.file_access_service import FileAccessDeniedError, FileAccessService
from app.services.lock_service import LockService
from app.services.path_service import PathResolver
from app.services.permission_service import PermissionService, ProtectedResource
from shared.enums import FileStatus

logger = logging.getLogger(__name__)

_NOT_ENOUGH_NODES = (
    "No es posible almacenar el archivo: no hay suficientes DataNodes "
    "disponibles para garantizar el factor de replicación 2."
)


class FileAlreadyExistsError(Exception):
    """Ya existe un archivo con ese nombre en el directorio (y no se pidió sobrescribir)."""


class FileBusyError(Exception):
    """El archivo existe pero todavía se está subiendo; no se puede sobrescribir."""


class UploadPermissionError(Exception):
    """El usuario no puede escribir en el directorio destino o en el archivo."""


class UploadFailedError(Exception):
    """La subida falló; no quedó ningún cambio visible."""


@dataclass(frozen=True)
class UploadResult:
    """Resultado de una subida exitosa."""

    file_id: int
    name: str
    size_bytes: int
    block_count: int
    overwritten: bool = False


def _stream_size(source: BinaryIO) -> int:
    """Tamaño del contenido recibido, sin leerlo a memoria."""
    source.seek(0, 2)
    size = source.tell()
    source.seek(0)
    return size


class UploadService:
    """Coordina la subida y distribución de un archivo con replicación factor 2."""

    def __init__(
        self,
        session: Session,
        block_size_bytes: int,
        max_retries: int,
        lock_timeout_seconds: int = 30,
    ) -> None:
        self._session = session
        self._files = FileRepository(session)
        self._blocks = BlockRepository(session)
        self._resolver = PathResolver(session)
        self._permissions = PermissionService(session)
        self._access = FileAccessService(session)
        self._locks = LockService(session, lock_timeout_seconds)
        self._writer = BlockWriter(session, block_size_bytes, max_retries)

    def _dir_resource(self, directory: Directory) -> ProtectedResource:
        return ProtectedResource(
            resource_type=RESOURCE_DIRECTORY,
            resource_id=directory.id,
            owner_id=directory.owner_id,
            group_id=directory.group_id,
            permissions=directory.permissions,
        )

    def upload(
        self,
        user: User,
        directory_path: str,
        filename: str,
        source: BinaryIO,
        overwrite: bool = False,
    ) -> UploadResult:
        """Sube ``source`` al directorio. Con ``overwrite`` reemplaza el existente."""
        directory = self._resolver.resolve(user, directory_path)
        existing = self._files.get_in_directory(directory.id, filename)

        if existing is not None:
            if not overwrite:
                raise FileAlreadyExistsError(filename)
            if existing.status != FileStatus.AVAILABLE:
                raise FileBusyError(filename)
            return self._overwrite(user, existing, source)

        if not self._permissions.can(user, self._dir_resource(directory), Action.WRITE):
            raise UploadPermissionError(directory_path)
        return self._create(user, directory, filename, source)

    # ------------------------------------------------------------------ PUT nuevo

    def _create(
        self, user: User, directory: Directory, filename: str, source: BinaryIO
    ) -> UploadResult:
        """Sube un archivo que no existía."""
        nodes = self._writer.eligible_nodes()
        try:
            self._writer.check_capacity(nodes, _stream_size(source))
        except NotEnoughEligibleNodesError as error:
            raise UploadFailedError(_NOT_ENOUGH_NODES) from error

        # Se confirma en UPLOADING antes de enviar bloques: si algo falla después,
        # el archivo existe de forma durable y se puede descartar.
        try:
            file = self._files.create(
                name=filename,
                directory_id=directory.id,
                owner_id=user.id,
                permissions=0o640,
            )
            self._session.commit()
        except IntegrityError as error:
            # Otro PUT con el mismo nombre ganó la carrera.
            self._session.rollback()
            raise FileAlreadyExistsError(filename) from error
        self._session.refresh(file)

        try:
            stored = self._writer.store_version(file.id, file.current_version, source, nodes)
        except Exception as error:
            self._session.rollback()
            self._discard_failed_file(file)
            if isinstance(error, (BlockSendError, NotEnoughEligibleNodesError)):
                raise UploadFailedError(
                    f"La subida del archivo {filename!r} falló: {error}"
                ) from error
            raise

        file.size_bytes = stored.size_bytes
        file.status = FileStatus.AVAILABLE
        self._session.commit()
        logger.info(
            "Archivo %r subido (%d bytes, %d bloques).",
            filename, stored.size_bytes, stored.block_count,
        )
        return UploadResult(
            file_id=file.id,
            name=file.name,
            size_bytes=stored.size_bytes,
            block_count=stored.block_count,
        )

    def _discard_failed_file(self, file: File) -> None:
        """Marca la subida como FAILED, libera el nombre y borra sus bloques.

        Lo que no se pueda borrar ahora (un nodo caído) lo termina la limpieza.
        """
        try:
            file.status = FileStatus.FAILED
            self._session.commit()
            DeleteService(self._session).purge(file)
        except Exception:
            self._session.rollback()
            logger.exception(
                "No se pudo limpiar la subida fallida del archivo %d; la limpieza lo reintentará.",
                file.id,
            )

    # ------------------------------------------------------- PUT con sobrescritura

    def _overwrite(self, user: User, file: File, source: BinaryIO) -> UploadResult:
        """Reemplaza el contenido de un archivo existente de forma segura."""
        try:
            self._access.require_file_permission(user, file, Action.WRITE)
        except FileAccessDeniedError as error:
            raise UploadPermissionError(file.name) from error

        nodes = self._writer.eligible_nodes()
        try:
            self._writer.check_capacity(nodes, _stream_size(source))
        except NotEnoughEligibleNodesError as error:
            raise UploadFailedError(_NOT_ENOUGH_NODES) from error

        # El lock impide que alguien lea, escriba o borre el archivo mientras se
        # reemplaza. Lanza FileLockedError (423) si otra sesión ya lo tiene.
        lock = self._locks.acquire(file, user.id, None)
        self._session.commit()

        try:
            stored = self._store_new_version(file, source, nodes, lock)
        finally:
            self._locks.release_lock(lock)
            self._session.commit()

        return UploadResult(
            file_id=file.id,
            name=file.name,
            size_bytes=stored.size_bytes,
            block_count=stored.block_count,
            overwritten=True,
        )

    def _store_new_version(self, file: File, source: BinaryIO, nodes, lock) -> StoredVersion:
        """Guarda la versión nueva, la activa y borra la vieja."""
        old_version = file.current_version
        new_version = self._blocks.next_version(file.id, old_version)

        def renew_lock() -> None:
            # Una subida grande no debe perder el lock a mitad de camino.
            self._locks.renew(lock)
            self._session.commit()

        try:
            stored = self._writer.store_version(
                file.id, new_version, source, nodes, on_block_stored=renew_lock
            )
        except Exception as error:
            self._session.rollback()
            self._writer.discard_version(file.id, new_version)
            if isinstance(error, (BlockSendError, NotEnoughEligibleNodesError)):
                raise UploadFailedError(
                    f"No se pudo sobrescribir {file.name!r} (la versión anterior "
                    f"sigue intacta): {error}"
                ) from error
            raise

        file.current_version = new_version
        file.size_bytes = stored.size_bytes
        self._session.commit()
        logger.info(
            "Archivo %r sobrescrito: versión %d activa (%d bytes, %d bloques).",
            file.name, new_version, stored.size_bytes, stored.block_count,
        )
        self._writer.discard_version(file.id, old_version)
        return stored
