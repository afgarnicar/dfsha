"""Gestión de los directorios personales de los usuarios.

Cada usuario tiene una carpeta personal en ``/home/<usuario>``, privada por
defecto (solo su propietario puede leer, escribir y atravesar). Este servicio
encapsula su creación, de modo que tanto el registro de usuarios como la
inicialización del sistema puedan garantizarla sin duplicar lógica.
"""

import logging

from sqlalchemy.orm import Session

from app.models.filesystem import Directory
from app.models.user import User
from app.repositories.directory_repository import DirectoryRepository

logger = logging.getLogger(__name__)

# Nombre del directorio que agrupa las carpetas personales.
HOME_PARENT_NAME = "home"

# Permisos de una carpeta personal: solo el propietario tiene rwx.
_HOME_PERMISSIONS = 0o700


class HomeParentMissingError(Exception):
    """Se lanza cuando no existe el directorio /home (debe crearlo el bootstrap)."""


class HomeDirectoryService:
    """Crea y garantiza las carpetas personales de los usuarios."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._directories = DirectoryRepository(session)

    def ensure_home_for(self, user: User) -> Directory:
        """Garantiza la carpeta personal del usuario, creándola si falta.

        La carpeta se crea en /home/<usuario> con permisos privados (0o700) y
        pertenece al propio usuario.
        """
        root = self._directories.get_root()
        if root is None:
            raise HomeParentMissingError("No existe el directorio raíz.")

        home_parent = self._directories.get_child(root.id, HOME_PARENT_NAME)
        if home_parent is None:
            raise HomeParentMissingError("No existe el directorio /home.")

        existing = self._directories.get_child(home_parent.id, user.username)
        if existing is not None:
            return existing

        home = self._directories.create(
            name=user.username,
            parent_id=home_parent.id,
            owner_id=user.id,
            group_id=None,
            permissions=_HOME_PERMISSIONS,
        )
        self._session.commit()
        self._session.refresh(home)
        logger.info("Carpeta personal creada para el usuario %s.", user.username)
        return home
