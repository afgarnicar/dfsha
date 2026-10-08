"""Inicialización idempotente del sistema de archivos.

Garantiza, al arrancar el ControlNode, que existan dos elementos imprescindibles:

- Un usuario de sistema, propietario del directorio raíz. No puede iniciar
  sesión: su hash de contraseña es un valor centinela que nunca coincide con
  ninguna contraseña real.
- El directorio raíz ``/``, propiedad del usuario de sistema, con permisos que
  permiten a todos leer y atravesar, pero solo al sistema escribir directamente
  en la raíz. Los usuarios crean su contenido en subdirectorios.

La operación es idempotente: si ya existen, no se duplican.
"""

import logging

from sqlalchemy.orm import Session

from app.models.filesystem import Directory
from app.models.user import User
from app.repositories.directory_repository import DirectoryRepository
from app.repositories.user_repository import UserRepository
from app.services.home_service import HomeDirectoryService

logger = logging.getLogger(__name__)

# Nombre del usuario de sistema, dueño del directorio raíz.
SYSTEM_USERNAME = "system"

# Valor centinela para el hash de contraseña del usuario de sistema. No tiene el
# formato de un hash Argon2, por lo que la verificación siempre falla y nadie
# puede autenticarse como este usuario.
_UNUSABLE_PASSWORD_HASH = "!"

# Nombre del directorio que agrupa las carpetas personales de los usuarios.
HOME_PARENT_NAME = "home"

# Permisos del directorio raíz: propietario rwx, grupo r-x, otros r-x.
_ROOT_PERMISSIONS = 0o755

# Permisos del directorio /home: todos pueden atravesarlo y listarlo, pero solo
# el sistema escribe en él directamente. Cada carpeta personal interna es privada.
_HOME_PARENT_PERMISSIONS = 0o755


class BootstrapService:
    """Asegura la existencia del usuario de sistema y del directorio raíz."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._directories = DirectoryRepository(session)

    def ensure_system_user(self) -> User:
        """Devuelve el usuario de sistema, creándolo si no existe."""
        existing = self._users.get_by_username(SYSTEM_USERNAME)
        if existing is not None:
            return existing
        user = self._users.create(SYSTEM_USERNAME, _UNUSABLE_PASSWORD_HASH)
        self._session.commit()
        self._session.refresh(user)
        logger.info("Usuario de sistema creado.")
        return user

    def ensure_root_directory(self) -> Directory:
        """Devuelve el directorio raíz, creándolo si no existe."""
        root = self._directories.get_root()
        if root is not None:
            return root

        system_user = self.ensure_system_user()
        root = self._directories.create(
            name="",
            parent_id=None,
            owner_id=system_user.id,
            group_id=None,
            permissions=_ROOT_PERMISSIONS,
        )
        self._session.commit()
        self._session.refresh(root)
        logger.info("Directorio raíz creado.")
        return root

    def ensure_home_parent(self, root: Directory) -> Directory:
        """Devuelve el directorio /home, creándolo si no existe."""
        existing = self._directories.get_child(root.id, HOME_PARENT_NAME)
        if existing is not None:
            return existing

        system_user = self.ensure_system_user()
        home_parent = self._directories.create(
            name=HOME_PARENT_NAME,
            parent_id=root.id,
            owner_id=system_user.id,
            group_id=None,
            permissions=_HOME_PARENT_PERMISSIONS,
        )
        self._session.commit()
        self._session.refresh(home_parent)
        logger.info("Directorio /home creado.")
        return home_parent

    def ensure_homes_for_existing_users(self) -> None:
        """Crea las carpetas personales que falten para los usuarios ya registrados.

        Cubre a los usuarios creados antes de que existiera el directorio /home.
        Se omite el usuario de sistema, que no tiene carpeta personal.
        """
        home_service = HomeDirectoryService(self._session)
        for user in self._users.list_all():
            if user.username == SYSTEM_USERNAME:
                continue
            home_service.ensure_home_for(user)

    def run(self) -> None:
        """Ejecuta el bootstrap completo del sistema de archivos."""
        self.ensure_system_user()
        root = self.ensure_root_directory()
        self.ensure_home_parent(root)
        self.ensure_homes_for_existing_users()
