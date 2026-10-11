"""Servicio de borrado de archivos (rm) y limpieza pendiente.

Flujo del rm:

1. Valida permiso de escritura en el directorio que contiene el archivo.
2. Marca el archivo como DELETING: desde ese momento ya no aparece en ls ni se
   puede descargar.
3. Pide a cada DataNode que borre sus réplicas de los bloques. Cada réplica
   borrada se quita de los metadatos.
4. Si todas las réplicas desaparecieron, borra el archivo de PostgreSQL.
   Si algún DataNode no respondió (por ejemplo, está OFFLINE), el archivo
   queda en DELETING con esas réplicas pendientes, y la limpieza periódica lo
   vuelve a intentar hasta terminar.

La misma limpieza recoge los archivos que quedaron FAILED tras una subida
fallida, para no dejar sus bloques regados en los DataNodes.

Al marcar un archivo para borrar, se le cambia el nombre interno (por ejemplo
``.~dfsha-borrando-15``). Así su nombre original queda libre de inmediato y el
usuario puede volver a subir un archivo con ese nombre, aunque la limpieza de
las réplicas todavía esté pendiente.
"""

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.models.filesystem import File
from app.models.user import User
from app.repositories.block_repository import BlockRepository
from app.repositories.file_repository import FileRepository
from app.security.permissions import Action
from app.services.file_access_service import FileAccessService
from shared.enums import FileStatus

logger = logging.getLogger(__name__)

# Tiempos de espera para borrar un bloque: es una operación pequeña.
_CONNECT_TIMEOUT_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 10.0

# Estados de archivo cuyos bloques hay que limpiar de los DataNodes.
CLEANUP_STATUSES = (FileStatus.DELETING, FileStatus.FAILED)

# Prefijo del nombre interno de un archivo marcado para borrar. Empieza por
# punto y lleva el id del archivo, así que no choca con nombres de usuarios.
DELETING_NAME_PREFIX = ".~dfsha-borrando-"


@dataclass(frozen=True)
class DeleteResult:
    """Resultado de borrar un archivo."""

    name: str
    completed: bool
    pending_replicas: int


class DeleteService:
    """Borra archivos y sus bloques de los DataNodes."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._access = FileAccessService(session)
        self._files = FileRepository(session)
        self._blocks = BlockRepository(session)

    def delete(self, user: User, path: str) -> DeleteResult:
        """Borra el archivo de la ruta indicada (operación rm)."""
        located = self._access.locate(user, path)
        self._access.require_directory_permission(user, located.directory, Action.WRITE)

        file = located.file
        original_name = file.name
        self._mark_for_deletion(file)
        return self.purge(file, display_name=original_name)

    def _mark_for_deletion(self, file: File) -> None:
        """Pasa el archivo a DELETING y libera su nombre original."""
        file.status = FileStatus.DELETING
        if not file.name.startswith(DELETING_NAME_PREFIX):
            file.name = f"{DELETING_NAME_PREFIX}{file.id}"
        self._session.commit()

    def purge(self, file: File, display_name: str | None = None) -> DeleteResult:
        """Borra de los DataNodes las réplicas que queden de un archivo.

        Si no queda ninguna, elimina el archivo de los metadatos. Se usa tanto
        en el rm como en la limpieza periódica.
        """
        name = display_name or file.name
        if file.status != FileStatus.DELETING:
            # Una subida FAILED también libera su nombre antes de limpiarse.
            self._mark_for_deletion(file)
        pending = 0
        timeout = httpx.Timeout(_REQUEST_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)

        with httpx.Client(timeout=timeout) as client:
            for block in self._blocks.list_all_for_file(file.id):
                for location in list(block.locations):
                    node = location.datanode
                    url = f"http://{node.host}:{node.port}/blocks/{block.storage_key}"
                    try:
                        response = client.delete(url)
                        deleted = response.status_code == 204
                        reason = f"HTTP {response.status_code}"
                    except httpx.HTTPError as error:
                        deleted = False
                        reason = type(error).__name__

                    if deleted:
                        self._blocks.delete_location(location)
                    else:
                        pending += 1
                        logger.warning(
                            "No se pudo borrar el bloque %s en %s (%s); queda pendiente.",
                            block.storage_key, node.name, reason,
                        )
                # Se confirma por bloque para no perder el progreso si algo falla.
                self._session.commit()

        if pending == 0:
            self._files.delete(file)
            self._session.commit()
            logger.info("Archivo %r eliminado por completo.", name)
            return DeleteResult(name=name, completed=True, pending_replicas=0)

        return DeleteResult(name=name, completed=False, pending_replicas=pending)

    def purge_pending(self) -> int:
        """Reintenta la limpieza de archivos DELETING o FAILED.

        Devuelve cuántos archivos quedaron eliminados por completo en esta pasada.
        """
        completed = 0
        for file in self._files.list_by_status(CLEANUP_STATUSES):
            try:
                if self.purge(file).completed:
                    completed += 1
            except Exception:
                # Un archivo problemático no debe frenar la limpieza de los demás.
                self._session.rollback()
                logger.exception("No se pudo limpiar el archivo con id %d.", file.id)
        return completed
