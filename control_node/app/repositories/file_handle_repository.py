"""Repositorio de los handles de archivo (sesiones de open/read/write/close)."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.access import FileHandle
from shared.enums import FileHandleMode


class FileHandleRepository:
    """Operaciones de persistencia sobre la tabla ``file_handles``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        token: str,
        file_id: int,
        user_id: int,
        mode: FileHandleMode,
        expires_at: datetime,
    ) -> FileHandle:
        """Crea un handle activo."""
        handle = FileHandle(
            handle_token=token,
            file_id=file_id,
            user_id=user_id,
            mode=mode,
            is_active=True,
            expires_at=expires_at,
        )
        self._session.add(handle)
        self._session.flush()  # Asigna el id, que necesita el lock.
        return handle

    def get_by_token(self, token: str) -> FileHandle | None:
        """Busca un handle por su token."""
        return self._session.scalars(
            select(FileHandle).where(FileHandle.handle_token == token)
        ).first()

    def deactivate_expired(self, now: datetime) -> int:
        """Marca como inactivos los handles vencidos. Devuelve cuántos cambió."""
        result = self._session.execute(
            update(FileHandle)
            .where(FileHandle.is_active.is_(True), FileHandle.expires_at <= now)
            .values(is_active=False)
        )
        return result.rowcount or 0
