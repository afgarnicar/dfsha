"""Repositorio de acceso a datos de bloques y sus ubicaciones."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

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
        storage_key: str,
    ) -> Block:
        """Crea un bloque en estado PENDING.

        ``storage_key`` es el nombre con el que el bloque se guarda en los
        DataNodes; se persiste para poder leerlo, borrarlo o replicarlo después.
        """
        block = Block(
            file_id=file_id,
            storage_key=storage_key,
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

    def list_for_file_version(self, file_id: int, version: int) -> list[Block]:
        """Bloques de una versión de un archivo, en orden, con sus réplicas y nodos."""
        statement = (
            select(Block)
            .where(Block.file_id == file_id, Block.version == version)
            .order_by(Block.block_index)
            .options(selectinload(Block.locations).selectinload(BlockLocation.datanode))
        )
        return list(self._session.scalars(statement).all())

    def list_all_for_file(self, file_id: int) -> list[Block]:
        """Todos los bloques de un archivo (cualquier versión), con sus réplicas."""
        statement = (
            select(Block)
            .where(Block.file_id == file_id)
            .order_by(Block.version, Block.block_index)
            .options(selectinload(Block.locations).selectinload(BlockLocation.datanode))
        )
        return list(self._session.scalars(statement).all())

    def list_blocks_on_datanode(self, datanode_id: int) -> list[Block]:
        """Bloques que tienen una réplica en el DataNode dado, con todas sus réplicas.

        Se usa en la re-replicación: cuando un nodo cae, hay que revisar los
        bloques que tenían una copia en él.
        """
        statement = (
            select(Block)
            .join(BlockLocation, BlockLocation.block_id == Block.id)
            .where(BlockLocation.datanode_id == datanode_id)
            .options(selectinload(Block.locations).selectinload(BlockLocation.datanode))
        )
        return list(self._session.scalars(statement).unique().all())

    def set_location_role(self, location: BlockLocation, role: ReplicaRole) -> None:
        """Cambia el rol (PRIMARY/BACKUP) de una réplica."""
        location.role = role

    def delete_location(self, location: BlockLocation) -> None:
        """Quita una réplica de los metadatos (ya se borró del DataNode)."""
        location.block.locations.remove(location)
        self._session.delete(location)

    def delete_block(self, block: Block) -> None:
        """Quita un bloque de los metadatos (ya no tiene réplicas en ningún nodo)."""
        self._session.delete(block)

    def next_version(self, file_id: int, current_version: int) -> int:
        """Número de versión siguiente a la más alta que tenga el archivo.

        Se usa la más alta registrada (no solo la actual) para no chocar con los
        bloques de un write anterior que haya fallado y aún no se hayan limpiado.
        """
        highest = self._session.scalar(
            select(func.max(Block.version)).where(Block.file_id == file_id)
        )
        return max(highest or 0, current_version) + 1
