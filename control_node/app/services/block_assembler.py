"""Trae bloques de los DataNodes y los deja descifrados en una carpeta temporal.

Es la pieza común de todas las lecturas: el GET (archivo completo), el read
(solo los bloques de un rango) y el write (que necesita el contenido actual
para aplicarle el cambio).

Para cada bloque: lo descarga en paralelo (PRIMARY primero, BACKUP si falla),
valida su SHA-256 y lo descifra. El descifrado se hace de a un bloque para que
la memoria usada no dependa del tamaño del archivo.
"""

from cryptography.fernet import InvalidToken

from app.models.filesystem import File
from app.models.storage import Block
from app.security.encryption import BlockCipher
from app.services.block_fetcher import BlockFetcher, BlockFetchPlan, ReplicaSource
from app.services.temp_storage_service import TempWorkspace, TemporaryStorageService
from shared.enums import NodeStatus, ReplicaRole


class FileIncompleteError(Exception):
    """Los metadatos del archivo no tienen todos sus bloques o réplicas."""


class FileDecryptionError(Exception):
    """Un bloque no se pudo descifrar (clave distinta o contenido alterado)."""


def encrypted_size(plaintext_size: int) -> int:
    """Tamaño exacto que ocupa un bloque cifrado con Fernet.

    Fernet arma un token de 57 bytes fijos + el contenido con relleno AES a
    múltiplos de 16, y lo entrega en base64 (4 caracteres por cada 3 bytes).
    Se usa para reservar el espacio temporal justo antes de descargar.
    """
    raw = 57 + (plaintext_size // 16 + 1) * 16
    return 4 * ((raw + 2) // 3)


def check_complete(file: File, blocks: list[Block]) -> None:
    """Verifica que estén todos los bloques, en orden y con al menos una réplica."""
    indexes = [block.block_index for block in blocks]
    if indexes != list(range(len(blocks))):
        raise FileIncompleteError(f"faltan bloques del archivo {file.name!r}")
    if sum(block.used_size_bytes for block in blocks) != file.size_bytes:
        raise FileIncompleteError(f"el tamaño de los bloques de {file.name!r} no cuadra")
    for block in blocks:
        if not block.locations:
            raise FileIncompleteError(
                f"el bloque {block.block_index + 1} de {file.name!r} no tiene réplicas"
            )


class BlockAssembler:
    """Descarga, valida y descifra bloques en una carpeta temporal."""

    def __init__(self, temp_storage: TemporaryStorageService, max_parallel_downloads: int) -> None:
        self._temp_storage = temp_storage
        self._fetcher = BlockFetcher(max_parallel_downloads)
        self._cipher = BlockCipher()

    @staticmethod
    def bytes_needed(blocks: list[Block]) -> int:
        """Espacio temporal para traer estos bloques.

        Durante la descarga conviven los bloques cifrados y, mientras se
        descifra, una parte en claro: se calcula para el peor momento.
        """
        encrypted_total = sum(encrypted_size(b.used_size_bytes) for b in blocks)
        largest_plain = max((b.used_size_bytes for b in blocks), default=0)
        return encrypted_total + largest_plain

    def reserve(self, payload_bytes: int) -> TempWorkspace:
        """Reserva espacio temporal y crea la carpeta de trabajo."""
        return self._temp_storage.reserve(payload_bytes)

    def assemble(self, blocks: list[Block], workspace: TempWorkspace) -> None:
        """Deja cada bloque descifrado en ``workspace.part_path(block_index)``."""
        if not blocks:
            return
        plans = [self._plan_for(block, workspace) for block in blocks]
        self._fetcher.fetch_all(plans)
        self._decrypt_parts(blocks, workspace)

    @staticmethod
    def _plan_for(block: Block, workspace: TempWorkspace) -> BlockFetchPlan:
        """Arma el plan de descarga de un bloque: PRIMARY primero, BACKUP después.

        Las réplicas en nodos OFFLINE se dejan de últimas: igual se intentan,
        por si el nodo ya volvió y el monitor todavía no lo ha visto.
        """
        def order(location) -> tuple[int, int]:
            offline = location.datanode.status == NodeStatus.OFFLINE
            backup = location.role != ReplicaRole.PRIMARY
            return (int(offline), int(backup))

        replicas = [
            ReplicaSource(
                node_name=location.datanode.name,
                host=location.datanode.host,
                port=location.datanode.port,
                role=location.role.value,
            )
            for location in sorted(block.locations, key=order)
        ]
        return BlockFetchPlan(
            block_index=block.block_index,
            storage_key=block.storage_key,
            checksum=block.checksum,
            target_path=workspace.part_path(block.block_index, "enc"),
            replicas=replicas,
        )

    def _decrypt_parts(self, blocks: list[Block], workspace: TempWorkspace) -> None:
        """Descifra cada bloque descargado, de a uno para limitar la memoria usada."""
        for block in blocks:
            encrypted_path = workspace.part_path(block.block_index, "enc")
            try:
                plaintext = self._cipher.decrypt(encrypted_path.read_bytes())
            except InvalidToken as error:
                raise FileDecryptionError(
                    f"no se pudo descifrar el bloque {block.block_index + 1}"
                ) from error
            if len(plaintext) != block.used_size_bytes:
                raise FileDecryptionError(
                    f"el bloque {block.block_index + 1} no tiene el tamaño esperado"
                )
            workspace.part_path(block.block_index).write_bytes(plaintext)
            encrypted_path.unlink()
            del plaintext
