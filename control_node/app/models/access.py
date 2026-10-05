"""Modelos de control de acceso y de sesiones de archivo.

Incluye las entradas de ACL para excepciones de permisos, los handles de
archivo abiertos (open/read/write/close) y los bloqueos de escritura.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from shared.enums import FileHandleMode, LockType


class AclEntry(Base):
    """Entrada de ACL que concede permisos específicos a un usuario o grupo.

    Permite excepciones por encima del modelo propietario/grupo/otros. El recurso
    puede ser un archivo o un directorio; el sujeto puede ser un usuario o un grupo.
    """

    __tablename__ = "acl_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Tipo de recurso al que aplica la entrada: "file" o "directory".
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Tipo de sujeto al que se concede el permiso: "user" o "group".
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    can_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FileHandle(Base):
    """Handle de una sesión de acceso a un archivo (open/read/write/close)."""

    __tablename__ = "file_handles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Token opaco que el cliente usa en read/write/close.
    handle_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    mode: Mapped[FileHandleMode] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileLock(Base):
    """Bloqueo de escritura exclusivo a nivel de archivo completo."""

    __tablename__ = "file_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Un archivo solo puede tener un bloqueo de escritura activo a la vez.
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    handle_id: Mapped[int | None] = mapped_column(
        ForeignKey("file_handles.id", ondelete="CASCADE"), nullable=True
    )
    lock_type: Mapped[LockType] = mapped_column(nullable=False, default=LockType.WRITE)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # El bloqueo se libera automáticamente al vencer este momento (timeout).
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
