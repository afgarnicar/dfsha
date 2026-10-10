"""Repositorio de acceso a datos de bloques y sus ubicaciones."""

from sqlalchemy.orm import Session

from app.models.storage import Block, BlockLocation
from shared.enums import BlockStatus, ReplicaRole


class BlockRepository:
    """Operaciones de persistencia sobre bloques y ubicaciones de réplicas."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_block(
        self,
        file_id: int,
        block_index: int,
        version: int,
        used_size_bytes: int,
        checksum: str,
    ) -> Block:
        """Crea un bloque en estado PENDING."""
        block = Block(
            file_id=file_id,
            block_index=block_index,
            version=version,
            used_size_bytes=used_size_bytes,
            checksum=checksum,
            status=BlockStatus.PENDING,
        )
        self._session.add(block)
        self._session.flush()  # Asigna el id del bloque para las ubicaciones.
        return block

    def add_location(
        self, block_id: int, datanode_id: int, role: ReplicaRole
    ) -> BlockLocation:
        """Registra una réplica de un bloque en un DataNode, en estado COMMITTED."""
        location = BlockLocation(
            block_id=block_id,
            datanode_id=datanode_id,
            role=role,
            status=BlockStatus.COMMITTED,
        )
        self._session.add(location)
        return location

    def mark_block_committed(self, block: Block) -> None:
        """Marca un bloque como COMMITTED (sus dos réplicas están almacenadas)."""
        block.status = BlockStatus.COMMITTED
