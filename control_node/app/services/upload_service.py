"""Servicio de subida de archivos (PUT).

Orquesta el flujo completo de subir un archivo al sistema distribuido:

1. Valida el permiso de escritura en el directorio destino.
2. Rechaza si el archivo ya existe o si no hay DataNodes suficientes.
3. Parte el archivo en bloques de tamaño fijo.
4. Cifra cada bloque, calcula su checksum y lo coloca en dos DataNodes
   distintos (PRIMARY y BACKUP) mediante la estrategia de distribución.
5. Si todos los bloques se almacenan con factor de replicación 2, marca el
   archivo como disponible; si algo falla, lo marca como fallido y limpia.

El contenido en claro nunca llega a los DataNodes: se cifra en este servicio
antes de enviarlo.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.distribution.placement_service import (
    EligibleNode,
    NotEnoughEligibleNodesError,
    PlacementService,
)
from app.distribution.strategy import RoundRobinStrategy
from app.models.filesystem import Directory, File
from app.models.user import User
from app.repositories.acl_repository import RESOURCE_DIRECTORY
from app.repositories.block_repository import BlockRepository
from app.repositories.datanode_repository import DataNodeRepository
from app.repositories.file_repository import FileRepository
from app.security.encryption import BlockCipher
from app.security.permissions import Action
from app.services.block_client import BlockClient, BlockSendError
from app.services.path_service import PathResolver
from app.services.permission_service import PermissionService, ProtectedResource
from shared.enums import FileStatus

logger = logging.getLogger(__name__)


class FileAlreadyExistsError(Exception):
    """Se lanza cuando ya existe un archivo con ese nombre en el directorio."""


class UploadPermissionError(Exception):
    """Se lanza cuando el usuario no puede escribir en el directorio destino."""


class UploadFailedError(Exception):
    """Se lanza cuando la subida falla y el archivo queda marcado como FAILED."""


@dataclass(frozen=True)
class UploadResult:
    """Resultado de una subida exitosa."""

    file_id: int
    name: str
    size_bytes: int
    block_count: int


class UploadService:
    """Coordina la subida y distribución de un archivo con replicación factor 2."""

    def __init__(self, session: Session, block_size_bytes: int, max_retries: int) -> None:
        self._session = session
        self._block_size_bytes = block_size_bytes
        self._files = FileRepository(session)
        self._blocks = BlockRepository(session)
        self._datanodes = DataNodeRepository(session)
        self._resolver = PathResolver(session)
        self._permissions = PermissionService(session)
        self._placement = PlacementService(RoundRobinStrategy())
        self._cipher = BlockCipher()
        self._block_client = BlockClient(max_retries=max_retries)

    def _dir_resource(self, directory: Directory) -> ProtectedResource:
        return ProtectedResource(
            resource_type=RESOURCE_DIRECTORY,
            resource_id=directory.id,
            owner_id=directory.owner_id,
            group_id=directory.group_id,
            permissions=directory.permissions,
        )

    def _eligible_nodes(self) -> list[EligibleNode]:
        """Lee los DataNodes registrados como candidatos de colocación."""
        return [
            EligibleNode(
                datanode_id=node.id,
                name=node.name,
                status=node.status,
                free_space_bytes=node.free_space_bytes,
            )
            for node in self._datanodes.list_all()
        ]

    def upload(
        self, user: User, directory_path: str, filename: str, content: bytes
    ) -> UploadResult:
        """Sube un archivo al directorio indicado y lo distribuye en bloques."""
        # 1. Resolver directorio destino y validar permiso de escritura.
        directory = self._resolver.resolve(user, directory_path)
        if not self._permissions.can(user, self._dir_resource(directory), Action.WRITE):
            raise UploadPermissionError(directory_path)

        # 2. Rechazar si el archivo ya existe (la sobrescritura es otra operación).
        if self._files.get_in_directory(directory.id, filename) is not None:
            raise FileAlreadyExistsError(filename)

        # 3. Partir el contenido en bloques del tamaño configurado.
        chunks = self._split_into_blocks(content)
        node_snapshot = self._eligible_nodes()

        # 4. Precondición: debe poder garantizarse factor 2. Se comprueba contra
        #    el tamaño del primer bloque (o 0 si el archivo es vacío); si no hay
        #    2 nodos elegibles, se rechaza la subida antes de crear nada.
        probe_size = len(chunks[0]) if chunks else 0
        try:
            self._placement.place_block(node_snapshot, 0, probe_size)
        except NotEnoughEligibleNodesError as error:
            raise UploadFailedError(
                "No es posible almacenar el archivo: no hay suficientes DataNodes "
                "disponibles para garantizar el factor de replicación 2."
            ) from error

        # 5. Crear y confirmar el archivo en estado UPLOADING. Se persiste de
        #    inmediato para que, si la subida de bloques falla después, el
        #    archivo exista de forma durable y pueda marcarse FAILED y limpiarse.
        file = self._files.create(
            name=filename,
            directory_id=directory.id,
            owner_id=user.id,
            permissions=0o640,
        )
        self._session.commit()
        self._session.refresh(file)

        try:
            total_size = self._store_blocks(file, chunks, node_snapshot)
        except (BlockSendError, NotEnoughEligibleNodesError) as error:
            # Algo falló: marcar el archivo como FAILED y limpiar sus bloques.
            self._mark_failed_and_cleanup(file)
            raise UploadFailedError(
                f"La subida del archivo {filename!r} falló: {error}"
            ) from error

        # 6. Todos los bloques quedaron con sus dos réplicas: archivo AVAILABLE.
        file.size_bytes = total_size
        file.status = FileStatus.AVAILABLE
        self._session.commit()
        self._session.refresh(file)

        return UploadResult(
            file_id=file.id,
            name=file.name,
            size_bytes=total_size,
            block_count=len(chunks),
        )

    def _split_into_blocks(self, content: bytes) -> list[bytes]:
        """Divide el contenido en trozos del tamaño de bloque configurado."""
        size = self._block_size_bytes
        if not content:
            return []
        return [content[i : i + size] for i in range(0, len(content), size)]

    def _store_blocks(
        self, file: File, chunks: list[bytes], nodes: list[EligibleNode]
    ) -> int:
        """Cifra, coloca y envía cada bloque a sus dos réplicas. Devuelve el tamaño total."""
        total_size = 0
        for index, chunk in enumerate(chunks):
            total_size += len(chunk)

            # Cifrar el bloque y calcular el checksum sobre el contenido cifrado.
            ciphertext = self._cipher.encrypt(chunk)
            checksum = hashlib.sha256(ciphertext).hexdigest()

            # Elegir los dos DataNodes (PRIMARY y BACKUP) para este bloque.
            placements = self._placement.place_block(nodes, index, len(ciphertext))

            # Identificador opaco del bloque, generado por el ControlNode.
            block_id = uuid.uuid4().hex

            block = self._blocks.create_block(
                file_id=file.id,
                block_index=index,
                version=file.current_version,
                used_size_bytes=len(chunk),
                checksum=checksum,
            )

            for placement in placements:
                node = self._datanodes.get_by_id(placement.datanode_id)
                self._block_client.send_block(
                    host=node.host,
                    port=node.port,
                    block_id=block_id,
                    checksum=checksum,
                    content=ciphertext,
                )
                self._blocks.add_location(block.id, placement.datanode_id, placement.role)

            self._blocks.mark_block_committed(block)
            # Confirma el bloque y sus ubicaciones antes de pasar al siguiente,
            # para que el progreso quede persistido de forma incremental.
            self._session.commit()

        return total_size

    def _mark_failed_and_cleanup(self, file: File) -> None:
        """Marca el archivo como FAILED tras un fallo de subida.

        El archivo queda FAILED (no visible como disponible). Los bloques ya
        creados se conservan en metadatos como parte del archivo fallido; una
        limpieza más profunda de bloques huérfanos en los DataNodes se aborda
        junto con la eliminación de archivos.
        """
        file.status = FileStatus.FAILED
        self._session.commit()
