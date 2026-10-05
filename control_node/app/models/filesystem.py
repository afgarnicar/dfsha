"""Modelos del sistema de archivos lógico: directorios y archivos.

Los directorios forman un árbol mediante ``parent_id``. Los archivos pertenecen
a un directorio y mantienen su estado del ciclo de vida y su versión actual.
Los permisos se guardan como un entero con el modo estilo Unix (por ejemplo
0o750), separando propietario, grupo y otros.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from shared.constants import MAX_NAME_LENGTH
from shared.enums import FileStatus


class Directory(Base):
    """Directorio lógico del sistema de archivos.

    El árbol se representa con ``parent_id``; el directorio raíz tiene
    ``parent_id`` nulo.
    """

    __tablename__ = "directories"
    __table_args__ = (
        # No puede haber dos directorios con el mismo nombre bajo el mismo padre.
        UniqueConstraint("parent_id", "name", name="uq_directory_parent_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id"), nullable=True
    )
    # Permisos en formato de modo Unix (owner/group/others rwx).
    permissions: Mapped[int] = mapped_column(Integer, nullable=False, default=0o750)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Subdirectorios directos de este directorio.
    children: Mapped[list["Directory"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["Directory | None"] = relationship(
        back_populates="children", remote_side="Directory.id"
    )
    # Archivos contenidos en este directorio.
    files: Mapped[list["File"]] = relationship(back_populates="directory")


class File(Base):
    """Archivo lógico. El contenido real vive en bloques distribuidos."""

    __tablename__ = "files"
    __table_args__ = (
        # No puede haber dos archivos con el mismo nombre en el mismo directorio.
        UniqueConstraint("directory_id", "name", name="uq_file_directory_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    directory_id: Mapped[int] = mapped_column(
        ForeignKey("directories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id"), nullable=True
    )
    permissions: Mapped[int] = mapped_column(Integer, nullable=False, default=0o640)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[FileStatus] = mapped_column(nullable=False, default=FileStatus.UPLOADING)
    # Versión activa del archivo. Las versiones solo existen para reemplazos
    # seguros durante la sobrescritura; no hay historial permanente.
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    directory: Mapped["Directory"] = relationship(back_populates="files")
    blocks: Mapped[list["Block"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )
