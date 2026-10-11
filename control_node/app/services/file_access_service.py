"""Ubicación de un archivo por su ruta lógica y control de acceso.

Convierte una ruta como ``/home/beto/libro.pdf`` en el archivo correspondiente,
validando que el usuario pueda atravesar los directorios del camino. Lo usan
las operaciones que trabajan sobre un archivo ya existente (GET, rm y, más
adelante, open/read/write).
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.filesystem import Directory, File
from app.models.user import User
from app.repositories.acl_repository import RESOURCE_DIRECTORY, RESOURCE_FILE
from app.repositories.file_repository import FileRepository
from app.security.permissions import Action
from app.services.path_service import PathResolver, split_path
from app.services.permission_service import PermissionService, ProtectedResource
from shared.enums import FileStatus


class FileNotFoundInPathError(Exception):
    """La ruta no corresponde a ningún archivo visible."""


class FileAccessDeniedError(Exception):
    """El usuario no tiene el permiso pedido sobre el archivo o su directorio."""


@dataclass(frozen=True)
class LocatedFile:
    """Archivo encontrado junto con el directorio que lo contiene."""

    file: File
    directory: Directory


class FileAccessService:
    """Busca archivos por ruta y valida los permisos sobre ellos."""

    def __init__(self, session: Session) -> None:
        self._files = FileRepository(session)
        self._resolver = PathResolver(session)
        self._permissions = PermissionService(session)

    def locate(self, user: User, path: str) -> LocatedFile:
        """Devuelve el archivo AVAILABLE al que apunta la ruta.

        Un archivo que se está subiendo, que falló o que se está borrando no es
        visible: para el usuario es como si no existiera.
        """
        segments = split_path(path)
        if not segments:
            raise FileNotFoundInPathError(path)

        directory_path = "/" + "/".join(segments[:-1])
        directory = self._resolver.resolve(user, directory_path)
        file = self._files.get_in_directory(directory.id, segments[-1])
        if file is None or file.status != FileStatus.AVAILABLE:
            raise FileNotFoundInPathError(path)
        return LocatedFile(file=file, directory=directory)

    def require_file_permission(self, user: User, file: File, action: Action) -> None:
        """Exige un permiso sobre el archivo (por ejemplo, lectura para GET)."""
        resource = ProtectedResource(
            resource_type=RESOURCE_FILE,
            resource_id=file.id,
            owner_id=file.owner_id,
            group_id=file.group_id,
            permissions=file.permissions,
        )
        if not self._permissions.can(user, resource, action):
            raise FileAccessDeniedError(file.name)

    def require_directory_permission(
        self, user: User, directory: Directory, action: Action
    ) -> None:
        """Exige un permiso sobre el directorio (por ejemplo, escritura para rm)."""
        resource = ProtectedResource(
            resource_type=RESOURCE_DIRECTORY,
            resource_id=directory.id,
            owner_id=directory.owner_id,
            group_id=directory.group_id,
            permissions=directory.permissions,
        )
        if not self._permissions.can(user, resource, action):
            raise FileAccessDeniedError(directory.name)
