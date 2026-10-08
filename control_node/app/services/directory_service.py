"""Servicio del sistema de archivos lógico.

Implementa las operaciones de directorios (mkdir, ls, cd, rmdir) sobre los
metadatos en PostgreSQL. No interactúa con los DataNodes: el filesystem lógico
vive por completo en la base de datos. Cada operación valida los permisos del
usuario con el servicio de autorización.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.filesystem import Directory, File
from app.models.user import User
from app.repositories.acl_repository import RESOURCE_DIRECTORY
from app.repositories.directory_repository import DirectoryRepository
from app.repositories.file_repository import FileRepository
from app.security.permissions import Action
from app.services.path_service import (
    PathResolver,
    split_path,
)
from app.services.permission_service import PermissionService, ProtectedResource


class DirectoryNotEmptyError(Exception):
    """Se lanza al intentar eliminar un directorio que no está vacío."""


class DirectoryAlreadyExistsError(Exception):
    """Se lanza al crear un directorio que ya existe bajo el mismo padre."""


class CannotRemoveRootError(Exception):
    """Se lanza al intentar eliminar el directorio raíz."""


class PermissionDeniedError(Exception):
    """Se lanza cuando el usuario no tiene permiso para la operación pedida."""


class InvalidPathError(Exception):
    """Se lanza cuando la ruta no es válida para la operación (por ejemplo vacía)."""


@dataclass(frozen=True)
class DirectoryListing:
    """Contenido de un directorio: sus subdirectorios y archivos."""

    directory: Directory
    subdirectories: Sequence[Directory]
    files: Sequence[File]


class DirectoryService:
    """Casos de uso de gestión del árbol de directorios lógicos."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._directories = DirectoryRepository(session)
        self._files = FileRepository(session)
        self._resolver = PathResolver(session)
        self._permissions = PermissionService(session)

    def _as_resource(self, directory: Directory) -> ProtectedResource:
        """Adapta un directorio al recurso protegido que entiende el servicio."""
        return ProtectedResource(
            resource_type=RESOURCE_DIRECTORY,
            resource_id=directory.id,
            owner_id=directory.owner_id,
            group_id=directory.group_id,
            permissions=directory.permissions,
        )

    def make_directory(
        self, user: User, path: str, permissions: int = 0o750
    ) -> Directory:
        """Crea un directorio en la ruta dada.

        El penúltimo componente de la ruta es el directorio padre (donde se
        crea); el último es el nombre del nuevo directorio. Requiere permiso de
        escritura en el padre. El creador queda como propietario.
        """
        segments = split_path(path)
        if not segments:
            # No se puede crear la raíz con mkdir; ya existe por configuración.
            raise InvalidPathError(path)

        parent_path = "/" + "/".join(segments[:-1])
        new_name = segments[-1]

        parent = self._resolver.resolve(user, parent_path)
        if not self._permissions.can(user, self._as_resource(parent), Action.WRITE):
            raise PermissionDeniedError(path)

        if self._directories.get_child(parent.id, new_name) is not None:
            raise DirectoryAlreadyExistsError(path)

        directory = self._directories.create(
            name=new_name,
            parent_id=parent.id,
            owner_id=user.id,
            # Un directorio nuevo no se asocia a ningún grupo por defecto.
            group_id=None,
            permissions=permissions,
        )
        self._session.commit()
        self._session.refresh(directory)
        return directory

    def list_directory(self, user: User, path: str) -> DirectoryListing:
        """Lista el contenido de un directorio.

        Requiere permiso de lectura sobre el directorio. Consulta la base de
        datos; no recorre los DataNodes.
        """
        directory = self._resolver.resolve(user, path)
        if not self._permissions.can(user, self._as_resource(directory), Action.READ):
            raise PermissionDeniedError(path)

        subdirectories = self._directories.list_children(directory.id)
        files = self._files.list_in_directory(directory.id)
        return DirectoryListing(
            directory=directory, subdirectories=subdirectories, files=files
        )

    def resolve_directory(self, user: User, path: str) -> Directory:
        """Valida que se puede entrar a un directorio (operación de 'cd').

        Requiere permiso de ejecución (atravesar) sobre el directorio destino,
        además del que ya exige la resolución de la ruta en el camino.
        """
        directory = self._resolver.resolve(user, path)
        if not self._permissions.can(user, self._as_resource(directory), Action.EXECUTE):
            raise PermissionDeniedError(path)
        return directory

    def remove_directory(self, user: User, path: str) -> None:
        """Elimina un directorio, solo si está vacío.

        Requiere permiso de escritura en el directorio padre. No elimina de
        forma recursiva: si contiene subdirectorios o archivos, falla.
        """
        segments = split_path(path)
        if not segments:
            raise CannotRemoveRootError(path)

        directory = self._resolver.resolve(user, path)

        # El padre se obtiene resolviendo la ruta sin el último componente.
        parent_path = "/" + "/".join(segments[:-1])
        parent = self._resolver.resolve(user, parent_path)
        if not self._permissions.can(user, self._as_resource(parent), Action.WRITE):
            raise PermissionDeniedError(path)

        if (
            self._directories.count_children(directory.id) > 0
            or self._directories.count_files(directory.id) > 0
        ):
            raise DirectoryNotEmptyError(path)

        self._directories.delete(directory)
        self._session.commit()
