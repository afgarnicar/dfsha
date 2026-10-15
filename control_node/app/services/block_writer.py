"""Escritura de una versión completa de un archivo en los DataNodes.

Es la pieza común del PUT, del PUT con ``--overwrite`` y del write:

1. Lee el contenido por bloques de 64 MB (sin cargar el archivo entero en
   memoria).
2. Cifra cada bloque y calcula su SHA-256 sobre el contenido cifrado.
3. Elige PRIMARY y BACKUP con la estrategia de distribución (Round Robin).
4. Guarda cada réplica. Si una réplica falla, conserva la que sí se guardó y
   prueba en OTRO DataNode elegible, hasta ``MAX_RETRIES`` intentos por réplica
   (como pide la especificación). Si no lo logra, la escritura falla: nunca se
   confirma un bloque con factor de replicación 1.

Cada bloque y cada réplica guardada se confirman en la base de datos de
inmediato. Así, si algo falla a mitad de camino, todo lo que alcanzó a llegar a
un DataNode está registrado y se puede borrar al descartar la versión.
"""

import hashlib
import logging
import uuid
from collections.abc import Callable
from itertools import cycle, islice
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.distribution.placement_service import (
    EligibleNode,
    PlacementService,
    ReplicaPlacement,
    is_eligible,
)
from app.distribution.strategy import RoundRobinStrategy
from app.repositories.block_repository import BlockRepository
from app.repositories.datanode_repository import DataNodeRepository
from app.security.encryption import BlockCipher
from app.services.block_client import BlockClient, BlockSendError
from app.services.replica_eraser import ReplicaEraser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredVersion:
    """Resultado de guardar una versión completa."""

    size_bytes: int
    block_count: int


class BlockWriter:
    """Guarda versiones de archivos en los DataNodes con factor de replicación 2."""

    def __init__(self, session: Session, block_size_bytes: int, max_retries: int) -> None:
        self._session = session
        self._block_size = block_size_bytes
        self._max_retries = max(1, max_retries)
        self._blocks = BlockRepository(session)
        self._datanodes = DataNodeRepository(session)
        self._eraser = ReplicaEraser(session)
        self._placement = PlacementService(RoundRobinStrategy())
        self._cipher = BlockCipher()
        # Un intento por llamada: los reintentos los maneja este servicio,
        # cambiando de nodo entre intentos.
        self._block_client = BlockClient(max_retries=1)
        self._on_progress: Callable[[], None] | None = None

    def eligible_nodes(self) -> list[EligibleNode]:
        """DataNodes registrados, como candidatos para colocar bloques."""
        return [
            EligibleNode(
                datanode_id=node.id,
                name=node.name,
                status=node.status,
                free_space_bytes=node.free_space_bytes,
            )
            for node in self._datanodes.list_all()
        ]

    def check_capacity(self, nodes: list[EligibleNode], total_size: int) -> None:
        """Falla si no hay 2 DataNodes elegibles (lanza NotEnoughEligibleNodesError)."""
        self._placement.place_block(nodes, 0, min(total_size, self._block_size))

    def store_version(
        self,
        file_id: int,
        version: int,
        source: BinaryIO,
        nodes: list[EligibleNode],
        on_progress: Callable[[], None] | None = None,
    ) -> StoredVersion:
        """Lee ``source`` por bloques y guarda cada uno con PRIMARY y BACKUP.

        ``on_progress`` se llama antes de cada intento de enviar una réplica y
        después de cada bloque. El write y el PUT con sobrescritura lo usan para
        renovar su lock: un DataNode caído puede tardar varios segundos en
        responder (hasta el timeout de conexión) y el lock no debe vencerse
        mientras tanto, porque la limpieza borraría la versión que se está
        escribiendo.
        """
        self._on_progress = on_progress
        try:
            total = 0
            index = 0
            while chunk := source.read(self._block_size):
                self._store_block(file_id, version, index, chunk, nodes)
                total += len(chunk)
                index += 1
                self._progress()
            return StoredVersion(size_bytes=total, block_count=index)
        finally:
            self._on_progress = None

    def _progress(self) -> None:
        if self._on_progress is not None:
            self._on_progress()

    def _store_block(
        self, file_id: int, version: int, index: int, chunk: bytes, nodes: list[EligibleNode]
    ) -> None:
        """Cifra un bloque y lo guarda en dos DataNodes distintos."""
        ciphertext = self._cipher.encrypt(chunk)
        checksum = hashlib.sha256(ciphertext).hexdigest()
        placements = self._placement.place_block(nodes, index, len(ciphertext))
        storage_key = uuid.uuid4().hex

        block = self._blocks.create_block(
            file_id=file_id,
            block_index=index,
            version=version,
            used_size_bytes=len(chunk),
            checksum=checksum,
            storage_key=storage_key,
        )
        self._session.commit()

        used: set[int] = {placement.datanode_id for placement in placements}
        for placement in placements:
            stored_on = self._store_replica(
                placement, storage_key, checksum, ciphertext, nodes, used
            )
            used.add(stored_on)
            self._blocks.add_location(block.id, stored_on, placement.role)
            self._session.commit()

        self._blocks.mark_block_committed(block)
        self._session.commit()

    def _store_replica(
        self,
        placement: ReplicaPlacement,
        storage_key: str,
        checksum: str,
        ciphertext: bytes,
        nodes: list[EligibleNode],
        used: set[int],
    ) -> int:
        """Guarda una réplica. Si su nodo falla, prueba en otro nodo elegible.

        Devuelve el id del DataNode donde quedó guardada. Lanza BlockSendError
        si tras ``max_retries`` intentos no se pudo guardar en ningún nodo.
        Los intentos recorren primero el nodo asignado y luego los alternos; si
        no hay alternos (solo 2 nodos en línea), se reintenta en el mismo nodo.
        """
        candidates = [placement.datanode_id] + [
            node.datanode_id
            for node in nodes
            if node.datanode_id not in used and is_eligible(node, len(ciphertext))
        ]
        errors: list[str] = []
        for datanode_id in islice(cycle(candidates), self._max_retries):
            self._progress()
            node = self._datanodes.get_by_id(datanode_id)
            try:
                self._block_client.send_block(
                    host=node.host,
                    port=node.port,
                    block_id=storage_key,
                    checksum=checksum,
                    content=ciphertext,
                )
            except BlockSendError as error:
                errors.append(f"{node.name}: {error}")
                logger.warning(
                    "Réplica %s del bloque %s falló en %s; se prueba en otro nodo.",
                    placement.role.value, storage_key, node.name,
                )
                continue
            if datanode_id != placement.datanode_id:
                logger.info(
                    "Réplica %s del bloque %s guardada en %s (nodo alterno).",
                    placement.role.value, storage_key, node.name,
                )
            return datanode_id

        raise BlockSendError(
            f"no se pudo guardar la réplica {placement.role.value} del bloque "
            f"tras {len(errors)} intento(s): " + " | ".join(errors)
        )

    def discard_version(self, file_id: int, version: int) -> None:
        """Borra los bloques de una versión (de los DataNodes y de los metadatos).

        Lo que no se pueda borrar ahora (un nodo caído) lo termina la limpieza.
        """
        blocks = self._blocks.list_for_file_version(file_id, version)
        if not blocks:
            return
        try:
            self._eraser.erase(blocks, drop_empty_blocks=True)
        except Exception:
            self._session.rollback()
            logger.exception(
                "No se pudo borrar la versión %d del archivo %d; la limpieza lo reintentará.",
                version, file_id,
            )
