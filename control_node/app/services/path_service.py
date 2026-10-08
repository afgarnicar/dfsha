"""Resolución de rutas lógicas a directorios.

Convierte una ruta como ``/documentos/universidad`` en el directorio
correspondiente, navegando el árbol desde la raíz. Para atravesar cada
directorio intermedio, el usuario debe tener permiso de ejecución (x), igual
que en un sistema de archivos Unix.
"""

from sqlalchemy.orm import Session

from app.models.filesystem import Directory
from app.models.user import User
from app.repositories.acl_repository import RESOURCE_DIRECTORY
from app.repositories.directory_repository import DirectoryRepository
from app.security.permissions import Action
from app.services.permission_service import PermissionService, ProtectedResource


class PathNotFoundError(Exception):
    """Se lanza cuando la ruta no corresponde a ningún directorio existente."""


class PathPermissionError(Exception):
    """Se lanza cuando el usuario no puede atravesar algún directorio de la ruta."""


class RootNotInitializedError(Exception):
    """Se lanza cuando no existe el directorio raíz (no debería ocurrir)."""


def split_path(path: str) -> list[str]:
    """Divide una ruta lógica en sus componentes, ignorando vacíos.

    "/a/b/c" -> ["a", "b", "c"]; "/" o "" -> [].
    """
    return [segment for segment in path.strip("/").split("/") if segment]


class PathResolver:
    """Resuelve rutas lógicas validando el permiso de atravesar en el camino."""

    def __init__(self, session: Session) -> None:
        self._directories = DirectoryRepository(session)
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

    def resolve(self, user: User, path: str) -> Directory:
        """Devuelve el directorio al que apunta la ruta.

        Valida permiso de atravesar (x) en la raíz y en cada directorio
        intermedio. Lanza PathNotFoundError si algún componente no existe y
        PathPermissionError si falta permiso para atravesar.
        """
        root = self._directories.get_root()
        if root is None:
            raise RootNotInitializedError()

        segments = split_path(path)

        # La raíz es el punto de partida; para descender por ella se exige x.
        current = root
        for index, name in enumerate(segments):
            if not self._permissions.can(user, self._as_resource(current), Action.EXECUTE):
                raise PathPermissionError(path)

            child = self._directories.get_child(current.id, name)
            if child is None:
                raise PathNotFoundError(path)
            current = child

        return current
