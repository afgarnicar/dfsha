"""Re-replicación automática de bloques cuando un DataNode cae.

Cuando un DataNode pasa a OFFLINE, los bloques que tenían una réplica en él
quedan con una sola copia (factor de replicación 1). Este servicio restaura el
factor 2:

1. Busca los bloques que tenían una réplica en el nodo caído.
2. Para cada uno, descarta la réplica perdida y revisa las que quedan en nodos
   en línea.
3. Si queda al menos una réplica válida, promueve a PRIMARY la que sobrevive (si
   la que se perdió era la PRIMARY) y ordena copiar el bloque a otro DataNode
   elegible, que entra como BACKUP.
4. Si no hay ningún otro DataNode elegible al que copiar, deja el bloque con
   factor 1 y lo registra como advertencia: es una limitación física, no un
   error. La revisión periódica (``restore_under_replicated``, que corre en la
   limpieza cada pocos segundos) lo vuelve a intentar hasta que haya un nodo
   disponible.

Solo se re-replican los bloques de la versión actual de archivos AVAILABLE: las
versiones viejas (de un write) y los archivos que se están borrando los quita la
limpieza, así que copiarlos sería trabajo perdido.

El ControlNode no mueve los bytes: le ordena al nodo que tiene la copia buena
que la envíe al nodo destino.
"""

import logging

from sqlalchemy.orm import Session

from app.distribution.placement_service import is_eligible, EligibleNode
from app.models.storage import Block, BlockLocation
from app.repositories.block_repository import BlockRepository
from app.repositories.datanode_repository import DataNodeRepository
from app.services.replication_order_client import (
    ReplicationOrderClient,
    ReplicationOrderError,
)
from shared.enums import FileStatus, NodeStatus, ReplicaRole

logger = logging.getLogger(__name__)

# Factor de replicación objetivo que la re-replicación intenta restaurar.
_TARGET_REPLICATION_FACTOR = 2


class RereplicationService:
    """Restaura el factor de replicación de los bloques afectados por un nodo caído."""

    def __init__(
        self,
        session: Session,
        order_client: ReplicationOrderClient | None = None,
    ) -> None:
        self._session = session
        self._blocks = BlockRepository(session)
        self._datanodes = DataNodeRepository(session)
        self._order_client = order_client or ReplicationOrderClient()

    def handle_node_offline(self, offline_datanode_id: int) -> None:
        """Restaura el factor de replicación de los bloques del nodo caído."""
        affected = self._blocks.list_blocks_on_datanode(offline_datanode_id)
        if not affected:
            return

        logger.info(
            "Re-replicación: %d bloque(s) afectados por la caída del nodo id=%d.",
            len(affected),
            offline_datanode_id,
        )
        for block in affected:
            if not self._is_live_block(block):
                continue
            try:
                self._rereplicate_block(block, offline_datanode_id)
            except ReplicationOrderError as error:
                # Un fallo en un bloque no debe frenar la re-replicación de los demás.
                logger.warning(
                    "No se pudo re-replicar el bloque %s: %s",
                    block.storage_key,
                    error,
                )

    @staticmethod
    def _is_live_block(block: Block) -> bool:
        """Indica si el bloque es de la versión actual de un archivo disponible."""
        file = block.file
        return file.status == FileStatus.AVAILABLE and block.version == file.current_version

    def restore_under_replicated(self) -> int:
        """Revisa todos los bloques y les crea las copias que les falten.

        Cubre los casos en que la re-replicación del momento de la caída no pudo
        terminar (por ejemplo, porque no había otro nodo disponible). Devuelve
        cuántas copias nuevas se crearon.
        """
        created = 0
        for block in self._blocks.list_current_blocks_of_available_files():
            # Una copia en un nodo SUSPECT todavía cuenta: puede ser una falla
            # pasajera, y por eso existe el umbral antes de declarar OFFLINE.
            # Solo se da por perdida la copia de un nodo OFFLINE.
            alive = [loc for loc in block.locations if loc.datanode.status != NodeStatus.OFFLINE]
            if len(alive) >= _TARGET_REPLICATION_FACTOR:
                continue
            live = [loc for loc in alive if loc.datanode.status == NodeStatus.ONLINE]
            if not live:
                continue
            try:
                if self._add_replica(block, live):
                    created += 1
            except ReplicationOrderError as error:
                self._session.rollback()
                logger.warning("No se pudo restaurar el bloque %s: %s", block.storage_key, error)
        return created

    def _add_replica(self, block: Block, live: list[BlockLocation]) -> bool:
        """Copia el bloque desde una réplica viva a un nodo nuevo.

        Si la PRIMARY quedó en un nodo caído, promueve la réplica viva a PRIMARY
        y deja la caída como BACKUP. Devuelve False si no hay nodo destino.
        """
        target = self._choose_target(
            block, exclude_datanode_ids={loc.datanode_id for loc in block.locations}
        )
        if target is None:
            return False

        source = live[0]
        if source.role != ReplicaRole.PRIMARY:
            for location in block.locations:
                if location.role == ReplicaRole.PRIMARY:
                    self._blocks.set_location_role(location, ReplicaRole.BACKUP)
            self._blocks.set_location_role(source, ReplicaRole.PRIMARY)

        source_node = self._datanodes.get_by_id(source.datanode_id)
        self._order_client.order_replication(
            source_host=source_node.host,
            source_port=source_node.port,
            storage_key=block.storage_key,
            target_host=target.host,
            target_port=target.port,
        )
        self._blocks.add_location(block.id, target.id, ReplicaRole.BACKUP)
        self._session.commit()
        logger.info(
            "Bloque %s: copia restaurada en el nodo %s (revisión periódica).",
            block.storage_key,
            target.name,
        )
        return True

    def _rereplicate_block(self, block: Block, offline_datanode_id: int) -> None:
        """Restaura el factor 2 de un bloque concreto."""
        # Ubicaciones del bloque en el nodo caído: su réplica ya no es accesible,
        # así que se quitan de los metadatos. Se recuerda si alguna era PRIMARY.
        lost_locations = [
            loc for loc in block.locations if loc.datanode_id == offline_datanode_id
        ]
        lost_primary = any(
            loc.role == ReplicaRole.PRIMARY for loc in lost_locations
        )

        # Réplicas que siguen vivas (en nodos en línea, excluyendo el caído).
        surviving = [
            loc
            for loc in block.locations
            if loc.datanode_id != offline_datanode_id
            and loc.datanode.status == NodeStatus.ONLINE
        ]

        if not surviving:
            logger.warning(
                "El bloque %s no tiene réplicas en línea; no se puede re-replicar ahora.",
                block.storage_key,
            )
            return

        # Si se perdió la PRIMARY, promover una superviviente a PRIMARY.
        source = surviving[0]
        if lost_primary and source.role != ReplicaRole.PRIMARY:
            self._blocks.set_location_role(source, ReplicaRole.PRIMARY)

        # Quitar de los metadatos las réplicas que estaban en el nodo caído.
        for lost in lost_locations:
            self._blocks.delete_location(lost)

        # Si ya hay suficientes réplicas vivas, no hace falta copiar.
        if len(surviving) >= _TARGET_REPLICATION_FACTOR:
            self._session.commit()
            return

        # Elegir un DataNode destino elegible que no tenga ya el bloque.
        target = self._choose_target(block, exclude_datanode_ids={
            loc.datanode_id for loc in block.locations
        })
        if target is None:
            logger.warning(
                "No hay un DataNode elegible para re-replicar el bloque %s; "
                "queda con factor de replicación 1 hasta que haya nodos disponibles.",
                block.storage_key,
            )
            self._session.commit()
            return

        # Ordenar al nodo que tiene la copia buena que la envíe al destino.
        source_node = self._datanodes.get_by_id(source.datanode_id)
        self._order_client.order_replication(
            source_host=source_node.host,
            source_port=source_node.port,
            storage_key=block.storage_key,
            target_host=target.host,
            target_port=target.port,
        )

        # Registrar la nueva réplica como BACKUP y persistir.
        self._blocks.add_location(block.id, target.id, ReplicaRole.BACKUP)
        self._session.commit()
        logger.info(
            "Bloque %s re-replicado en el nodo %s; factor de replicación restaurado.",
            block.storage_key,
            target.name,
        )

    def _choose_target(self, block: Block, exclude_datanode_ids: set[int]):
        """Elige un DataNode en línea, con espacio y que no tenga ya el bloque."""
        block_size = block.used_size_bytes
        for node in self._datanodes.list_all():
            if node.id in exclude_datanode_ids:
                continue
            candidate = EligibleNode(
                datanode_id=node.id,
                name=node.name,
                status=node.status,
                free_space_bytes=node.free_space_bytes,
            )
            if is_eligible(candidate, block_size):
                return node
        return None
