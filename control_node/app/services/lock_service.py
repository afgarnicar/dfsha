"""Bloqueo de escritura exclusivo sobre un archivo completo (WRITE LOCK).

Reglas:

- Solo una sesión puede tener el lock de un archivo a la vez. La tabla
  ``file_locks`` lo garantiza con una restricción única sobre ``file_id``.
- Mientras el lock está activo, nadie más puede leer, escribir ni borrar el
  archivo.
- El lock vence a los ``LOCK_TIMEOUT_SECONDS``. Cada escritura lo renueva. Si el
  cliente desaparece sin cerrar, el lock vencido se considera libre y se borra
  la próxima vez que alguien lo consulte (o en la limpieza periódica).

No se implementan locks por bloque: el lock es del archivo completo.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.access import FileLock
from app.models.filesystem import File
from shared.enums import LockType


class FileLockedError(Exception):
    """El archivo tiene un lock de escritura activo de otra sesión."""


def utcnow() -> datetime:
    """Hora actual con zona horaria (UTC)."""
    return datetime.now(timezone.utc)


class LockService:
    """Adquiere, renueva, consulta y libera locks de escritura."""

    def __init__(self, session: Session, lock_timeout_seconds: int) -> None:
        self._session = session
        self._timeout = timedelta(seconds=lock_timeout_seconds)

    def acquire(self, file: File, user_id: int, handle_id: int) -> FileLock:
        """Toma el lock de escritura del archivo para un handle.

        Bloquea la fila del archivo mientras decide (SELECT ... FOR UPDATE), así
        dos sesiones que piden el lock al mismo tiempo no pueden ganar las dos.
        Lanza FileLockedError si otra sesión tiene un lock vigente.
        """
        self._session.execute(select(File.id).where(File.id == file.id).with_for_update())

        current = self.get_lock(file.id)
        if current is not None:
            if current.expires_at > utcnow():
                raise FileLockedError(file.name)
            # Lock vencido: el cliente anterior desapareció, se libera.
            self._session.delete(current)
            self._session.flush()

        now = utcnow()
        lock = FileLock(
            file_id=file.id,
            user_id=user_id,
            handle_id=handle_id,
            lock_type=LockType.WRITE,
            acquired_at=now,
            expires_at=now + self._timeout,
        )
        self._session.add(lock)
        self._session.flush()
        return lock

    def get_lock(self, file_id: int) -> FileLock | None:
        """Lock registrado para el archivo (vigente o vencido), o None."""
        return self._session.scalars(
            select(FileLock).where(FileLock.file_id == file_id)
        ).first()

    def active_lock(self, file_id: int) -> FileLock | None:
        """Lock vigente del archivo, o None si no hay o ya venció."""
        lock = self.get_lock(file_id)
        if lock is None or lock.expires_at <= utcnow():
            return None
        return lock

    def ensure_not_locked(self, file_id: int, handle_id: int | None = None) -> None:
        """Falla si otra sesión tiene el lock vigente.

        Si ``handle_id`` es el dueño del lock, no cuenta como bloqueo.
        """
        lock = self.active_lock(file_id)
        if lock is not None and lock.handle_id != handle_id:
            raise FileLockedError(str(file_id))

    def holds_lock(self, file_id: int, handle_id: int) -> FileLock | None:
        """Devuelve el lock si este handle lo tiene vigente; si no, None."""
        lock = self.active_lock(file_id)
        if lock is not None and lock.handle_id == handle_id:
            return lock
        return None

    def renew(self, lock: FileLock) -> datetime:
        """Extiende el vencimiento del lock. Devuelve el nuevo vencimiento."""
        lock.expires_at = utcnow() + self._timeout
        self._session.flush()
        return lock.expires_at

    def release(self, file_id: int, handle_id: int) -> None:
        """Libera el lock del archivo si lo tiene este handle."""
        self._session.execute(
            delete(FileLock).where(FileLock.file_id == file_id, FileLock.handle_id == handle_id)
        )
        self._session.flush()

    def remove_expired(self) -> int:
        """Borra los locks vencidos. Devuelve cuántos se borraron."""
        result = self._session.execute(delete(FileLock).where(FileLock.expires_at <= utcnow()))
        return result.rowcount or 0
