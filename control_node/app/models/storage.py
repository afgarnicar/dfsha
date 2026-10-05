"""Modelos de almacenamiento: bloques, ubicaciones de réplicas y DataNodes.

Un archivo se divide en bloques ordenados por ``block_index``. Cada bloque se
replica en dos DataNodes distintos; esas ubicaciones se guardan de forma
normalizada en ``block_locations`` con un rol PRIMARY o BACKUP, en lugar de
columnas fijas en la tabla de bloques.
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
from shared.enums import BlockStatus, NodeStatus, ReplicaRole


class DataNode(Base):
    """Nodo de almacenamiento físico de bloques."""

    __tablename__ = "datanodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(MAX_NAME_LENGTH), unique=True, nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[NodeStatus] = mapped_column(nullable=False, default=NodeStatus.ONLINE)
    free_space_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Momento de la última verificación de salud exitosa o fallida.
    last_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Contador de verificaciones fallidas consecutivas para el monitor de nodos.
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    locations: Mapped[list["BlockLocation"]] = relationship(back_populates="datanode")


class Block(Base):
    """Bloque de 64 MB lógicos de un archivo."""

    __tablename__ = "blocks"
    __table_args__ = (
        # Dentro de una versión del archivo, cada índice de bloque es único.
        UniqueConstraint(
            "file_id", "version", "block_index", name="uq_block_file_version_index"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Posición del bloque dentro del archivo; determina el orden de reconstrucción.
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Versión del archivo a la que pertenece el bloque (para sobrescritura segura).
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Bytes realmente usados; el último bloque puede estar parcialmente lleno.
    used_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Checksum SHA-256 del contenido del bloque, en hexadecimal.
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[BlockStatus] = mapped_column(
        nullable=False, default=BlockStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    file: Mapped["File"] = relationship(back_populates="blocks")
    locations: Mapped[list["BlockLocation"]] = relationship(
        back_populates="block", cascade="all, delete-orphan"
    )


class BlockLocation(Base):
    """Ubicación de una réplica de un bloque en un DataNode concreto."""

    __tablename__ = "block_locations"
    __table_args__ = (
        # Un bloque no puede tener dos réplicas en el mismo DataNode.
        UniqueConstraint("block_id", "datanode_id", name="uq_location_block_datanode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_id: Mapped[int] = mapped_column(
        ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    datanode_id: Mapped[int] = mapped_column(
        ForeignKey("datanodes.id"), nullable=False, index=True
    )
    role: Mapped[ReplicaRole] = mapped_column(nullable=False)
    status: Mapped[BlockStatus] = mapped_column(
        nullable=False, default=BlockStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    block: Mapped["Block"] = relationship(back_populates="locations")
    datanode: Mapped["DataNode"] = relationship(back_populates="locations")
