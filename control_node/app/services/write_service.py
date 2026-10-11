"""Escritura sobre un archivo abierto: write(handle, offset, data).

Aunque la interfaz recibe un offset, NO se modifican bloques sueltos. Para
mantener la consistencia, cada write reemplaza el archivo completo de forma
segura (decisión deliberada de la especificación, documentada como limitación):

1. Valida el handle y que tenga el WRITE LOCK vigente (y lo renueva).
2. Valida el offset: se permite modificar dentro del archivo o escribir
   exactamente al final (EOF) para hacerlo crecer. No se permite un offset
   mayor que el tamaño actual (no hay archivos con huecos).
3. Reconstruye el archivo actual en almacenamiento temporal.
4. Aplica el cambio y obtiene la versión nueva completa.
5. La parte en bloques de 64 MB, los cifra y los distribuye con Round Robin en
   PRIMARY + BACKUP (factor 2 obligatorio), como una versión nueva.
6. Solo cuando TODOS los bloques nuevos están guardados, activa la versión
   nueva y borra la vieja.

Si algo falla, la versión vieja sigue siendo la activa y se borran los bloques
que alcanzó a crear la versión nueva.
"""

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.distribution.placement_service import (
    EligibleNode,
    NotEnoughEligibleNodesError,
    PlacementService,
)
from app.distribution.strategy import RoundRobinStrategy
from app.models.storage import Block
from app.models.user import User
from app.repositories.block_repository import BlockRepository
from app.repositories.datanode_repository import DataNodeRepository
from app.security.encryption import BlockCipher
from app.services.block_assembler import BlockAssembler, check_complete
from app.services.block_client import BlockClient, BlockSendError
from app.services.handle_service import HandleService, ValidHandle
from app.services.read_service import InvalidRangeError, slices_for_range
from app.services.replica_eraser import ReplicaEraser
from app.services.temp_storage_service import TempWorkspace, TemporaryStorageService

logger = logging.getLogger(__name__)

# Tamaño de cada lectura al copiar contenido entre archivos temporales.
_COPY_CHUNK_BYTES = 1024 * 1024


class WriteFailedError(Exception):
    """La escritura no se pudo completar; la versión anterior sigue activa."""


@dataclass(frozen=True)
class WriteResult:
    """Resultado de una escritura exitosa."""

    size_bytes: int
    version: int
    block_count: int
    expires_at: datetime


def _data_size(data: BinaryIO) -> int:
    """Tamaño del contenido recibido, sin leerlo a memoria."""
    data.seek(0, 2)
    size = data.tell()
    data.seek(0)
    return size


class WriteService:
    """Aplica escrituras reemplazando el archivo completo por una versión nueva."""

    def __init__(
        self,
        session: Session,
        handles: HandleService,
        temp_storage: TemporaryStorageService,
        max_parallel_downloads: int,
        block_size_bytes: int,
        max_retries: int,
    ) -> None:
        self._session = session
        self._handles = handles
        self._blocks = BlockRepository(session)
        self._datanodes = DataNodeRepository(session)
        self._assembler = BlockAssembler(temp_storage, max_parallel_downloads)
        self._eraser = ReplicaEraser(session)
        self._placement = PlacementService(RoundRobinStrategy())
        self._cipher = BlockCipher()
        self._block_client = BlockClient(max_retries=max_retries)
        self._block_size = block_size_bytes

    def write(self, user: User, token: str, offset: int, data: BinaryIO) -> WriteResult:
        """Escribe ``data`` desde ``offset`` y activa la versión nueva del archivo."""
        valid = self._handles.validate(user, token)
        HandleService.ensure_can_write(valid)
        self._handles.touch(valid)
        file = valid.file

        if offset < 0:
            raise InvalidRangeError("el offset no puede ser negativo")
        if offset > file.size_bytes:
            raise InvalidRangeError(
                f"el offset {offset} está después del final del archivo "
                f"({file.size_bytes} bytes); no se permiten huecos"
            )

        data_size = _data_size(data)
        new_size = max(file.size_bytes, offset + data_size)
        old_blocks = self._blocks.list_for_file_version(file.id, file.current_version)
        check_complete(file, old_blocks)

        # Precondición: debe poder garantizarse el factor de replicación 2.
        nodes = self._eligible_nodes()
        try:
            self._placement.place_block(nodes, 0, min(new_size, self._block_size))
        except NotEnoughEligibleNodesError as error:
            raise WriteFailedError(
                "no hay suficientes DataNodes disponibles para garantizar el factor "
                "de replicación 2"
            ) from error

        # Temporal: bloques viejos (cifrados y en claro) + el archivo nuevo completo.
        workspace = self._assembler.reserve(
            BlockAssembler.bytes_needed(old_blocks) + file.size_bytes + new_size
        )
        try:
            self._assembler.assemble(old_blocks, workspace)
            new_path = workspace.path / "nuevo.bin"
            self._build_new_content(old_blocks, workspace, new_path, offset, data, data_size)
            return self._store_new_version(valid, new_path, new_size, nodes)
        finally:
            workspace.release()

    def _build_new_content(
        self,
        old_blocks: list[Block],
        workspace: TempWorkspace,
        new_path: Path,
        offset: int,
        data: BinaryIO,
        data_size: int,
    ) -> None:
        """Arma en disco: contenido viejo antes del offset + data + resto viejo."""
        old_size = sum(block.used_size_bytes for block in old_blocks)
        with new_path.open("wb") as target:
            self._copy_old_range(old_blocks, workspace, target, 0, offset)
            shutil.copyfileobj(data, target, _COPY_CHUNK_BYTES)
            tail_start = offset + data_size
            if tail_start < old_size:
                self._copy_old_range(old_blocks, workspace, target, tail_start, old_size - tail_start)

    @staticmethod
    def _copy_old_range(
        old_blocks: list[Block], workspace: TempWorkspace, target: BinaryIO, start: int, length: int
    ) -> None:
        """Copia un rango del contenido viejo (ya descifrado) al archivo nuevo."""
        if length <= 0:
            return
        for piece in slices_for_range(old_blocks, start, length):
            with workspace.part_path(piece.block_index).open("rb") as part:
                part.seek(piece.start)
                remaining = piece.end - piece.start
                while remaining > 0:
                    chunk = part.read(min(_COPY_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    target.write(chunk)
                    remaining -= len(chunk)

    def _eligible_nodes(self) -> list[EligibleNode]:
        """DataNodes registrados, como candidatos para colocar los bloques nuevos."""
        return [
            EligibleNode(
                datanode_id=node.id,
                name=node.name,
                status=node.status,
                free_space_bytes=node.free_space_bytes,
            )
            for node in self._datanodes.list_all()
        ]

    def _store_new_version(
        self, valid: ValidHandle, new_path: Path, new_size: int, nodes: list[EligibleNode]
    ) -> WriteResult:
        """Guarda la versión nueva bloque por bloque y la activa si todo sale bien."""
        file = valid.file
        old_version = file.current_version
        new_version = self._blocks.next_version(file.id, old_version)
        block_count = 0

        try:
            with new_path.open("rb") as source:
                index = 0
                while chunk := source.read(self._block_size):
                    self._store_block(file.id, new_version, index, chunk, nodes)
                    # Cada bloque guardado renueva el lock: un write largo no lo pierde.
                    self._handles.touch(valid)
                    index += 1
                block_count = index
        except Exception as error:
            self._session.rollback()
            self._discard_version(file.id, new_version)
            if isinstance(error, (BlockSendError, NotEnoughEligibleNodesError)):
                raise WriteFailedError(str(error)) from error
            raise

        # Todos los bloques nuevos tienen sus dos réplicas: se activa la versión nueva.
        file.current_version = new_version
        file.size_bytes = new_size
        self._session.commit()
        logger.info(
            "Archivo %r: versión %d activada (%d bytes, %d bloques).",
            file.name, new_version, new_size, block_count,
        )

        # La versión vieja ya no se usa: se borra. Lo que no se pueda borrar ahora
        # (un nodo caído) lo termina la limpieza periódica.
        self._discard_version(file.id, old_version)

        return WriteResult(
            size_bytes=new_size,
            version=new_version,
            block_count=block_count,
            expires_at=valid.handle.expires_at,
        )

    def _store_block(
        self, file_id: int, version: int, index: int, chunk: bytes, nodes: list[EligibleNode]
    ) -> None:
        """Cifra un bloque y lo guarda en PRIMARY y BACKUP."""
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
        # Se confirma antes de enviar y después de cada réplica guardada: si una
        # réplica falla, las que sí quedaron en un DataNode están anotadas y se
        # pueden borrar al descartar la versión.
        self._session.commit()
        for placement in placements:
            node = self._datanodes.get_by_id(placement.datanode_id)
            self._block_client.send_block(
                host=node.host,
                port=node.port,
                block_id=storage_key,
                checksum=checksum,
                content=ciphertext,
            )
            self._blocks.add_location(block.id, placement.datanode_id, placement.role)
            self._session.commit()
        self._blocks.mark_block_committed(block)
        self._session.commit()

    def _discard_version(self, file_id: int, version: int) -> None:
        """Borra los bloques de una versión (de los DataNodes y de los metadatos)."""
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
