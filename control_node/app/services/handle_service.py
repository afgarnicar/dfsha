"""Sesiones de acceso a archivos: open y close, y validación de handles.

``open(path, mode)`` no descarga nada: valida el archivo, los permisos y los
locks, y entrega un ``handle`` (un token opaco). Después el cliente usa ese
token en read, write y close.

- Modo READ: exige permiso de lectura y que nadie tenga el lock de escritura.
  El handle vence a los ``HANDLE_TIMEOUT_SECONDS`` sin uso; cada read lo renueva.
- Modo WRITE: exige permiso de escritura y toma el lock exclusivo del archivo.
  El handle vence junto con el lock (``LOCK_TIMEOUT_SECONDS``); cada write lo
  renueva. Si el cliente desaparece, el lock se libera solo al vencer.

``close(handle)`` invalida el handle y, si era de escritura, libera el lock.
Después de cerrar, read y write con ese handle fallan.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.access import FileHandle
from app.models.filesystem import File
from app.models.user import User
from app.repositories.file_handle_repository import FileHandleRepository
from app.security.permissions import Action
from app.services.file_access_service import FileAccessService
from app.services.lock_service import FileLockedError, LockService, utcnow
from shared.enums import FileHandleMode, FileStatus


class HandleInvalidError(Exception):
    """El handle no existe, está cerrado, venció o es de otro usuario."""


class HandleModeError(Exception):
    """La operación no está permitida con el modo en que se abrió el handle."""


@dataclass(frozen=True)
class OpenedHandle:
    """Datos que se le devuelven al cliente al abrir un archivo."""

    token: str
    mode: FileHandleMode
    file_name: str
    size_bytes: int
    expires_at: datetime


@dataclass(frozen=True)
class ValidHandle:
    """Handle validado junto con su archivo."""

    handle: FileHandle
    file: File


class HandleService:
    """Abre, valida, renueva y cierra handles de archivo."""

    def __init__(
        self, session: Session, lock_timeout_seconds: int, handle_timeout_seconds: int
    ) -> None:
        self._session = session
        self._access = FileAccessService(session)
        self._handles = FileHandleRepository(session)
        self._locks = LockService(session, lock_timeout_seconds)
        self._read_timeout = timedelta(seconds=handle_timeout_seconds)

    def open(self, user: User, path: str, mode: FileHandleMode) -> OpenedHandle:
        """Abre un archivo y devuelve su handle."""
        located = self._access.locate(user, path)
        file = located.file
        token = secrets.token_urlsafe(32)

        if mode == FileHandleMode.READ:
            self._access.require_file_permission(user, file, Action.READ)
            self._locks.ensure_not_locked(file.id)
            handle = self._handles.create(
                token, file.id, user.id, mode, utcnow() + self._read_timeout
            )
        else:
            self._access.require_file_permission(user, file, Action.WRITE)
            # Primero el candado del archivo y después el handle: así dos open
            # WRITE simultáneos se ordenan en vez de bloquearse mutuamente.
            self._locks.lock_file_row(file)
            handle = self._handles.create(token, file.id, user.id, mode, utcnow())
            try:
                lock = self._locks.acquire(file, user.id, handle.id)
            except FileLockedError:
                self._session.rollback()
                raise
            handle.expires_at = lock.expires_at

        self._session.commit()
        return OpenedHandle(
            token=token,
            mode=mode,
            file_name=file.name,
            size_bytes=file.size_bytes,
            expires_at=handle.expires_at,
        )

    def validate(self, user: User, token: str) -> ValidHandle:
        """Devuelve el handle si sigue siendo válido para este usuario.

        Un handle de escritura deja de ser válido si su lock venció (por
        ejemplo, si pasó más del timeout sin escribir).
        """
        handle = self._handles.get_by_token(token)
        if (
            handle is None
            or not handle.is_active
            or handle.user_id != user.id
            or handle.expires_at is None
            or handle.expires_at <= utcnow()
        ):
            raise HandleInvalidError(token)

        file = self._session.get(File, handle.file_id)
        if file is None or file.status != FileStatus.AVAILABLE:
            raise HandleInvalidError(token)

        if handle.mode == FileHandleMode.WRITE and self._locks.holds_lock(file.id, handle.id) is None:
            raise HandleInvalidError(token)
        return ValidHandle(handle=handle, file=file)

    def ensure_can_read(self, user: User, valid: ValidHandle) -> None:
        """Valida que con este handle se pueda leer ahora mismo.

        Un handle de lectura no puede leer mientras otra sesión tiene el lock de
        escritura. Un handle de escritura puede leer su propio archivo si el
        usuario tiene permiso de lectura.
        """
        self._locks.ensure_not_locked(valid.file.id, handle_id=valid.handle.id)
        if valid.handle.mode == FileHandleMode.WRITE:
            self._access.require_file_permission(user, valid.file, Action.READ)

    @staticmethod
    def ensure_can_write(valid: ValidHandle) -> None:
        """Valida que el handle se haya abierto en modo escritura."""
        if valid.handle.mode != FileHandleMode.WRITE:
            raise HandleModeError("el handle se abrió solo para lectura")

    def touch(self, valid: ValidHandle) -> datetime:
        """Renueva el vencimiento del handle (y del lock, si es de escritura)."""
        handle = valid.handle
        if handle.mode == FileHandleMode.WRITE:
            lock = self._locks.holds_lock(valid.file.id, handle.id)
            if lock is None:
                raise HandleInvalidError(handle.handle_token)
            handle.expires_at = self._locks.renew(lock)
        else:
            handle.expires_at = utcnow() + self._read_timeout
        self._session.commit()
        return handle.expires_at

    def close(self, user: User, token: str) -> None:
        """Cierra el handle y libera el lock si era de escritura.

        Cerrar un handle de escritura cuyo lock ya venció también es válido:
        simplemente lo marca como cerrado.
        """
        handle = self._handles.get_by_token(token)
        if handle is None or not handle.is_active or handle.user_id != user.id:
            raise HandleInvalidError(token)

        handle.is_active = False
        if handle.mode == FileHandleMode.WRITE:
            self._locks.release(handle.file_id, handle.id)
        self._session.commit()

    def sweep_expired(self) -> tuple[int, int]:
        """Cierra handles vencidos y borra locks vencidos (limpieza periódica)."""
        handles = self._handles.deactivate_expired(utcnow())
        locks = self._locks.remove_expired()
        self._session.commit()
        return handles, locks
