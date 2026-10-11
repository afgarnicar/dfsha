"""Lectura de un rango de un archivo abierto: read(handle, offset, length).

A diferencia del GET, que trae el archivo completo, el read solo descarga los
bloques que contienen el rango pedido. Por ejemplo, con bloques de 64 MB:

- offset = 70 MB, length = 10 MB  -> solo el bloque 2;
- offset = 60 MB, length = 10 MB  -> los últimos 4 MB del bloque 1 y los
  primeros 6 MB del bloque 2.

Cada bloque se pide a la réplica PRIMARY y, si falla, a la BACKUP, y se valida
su SHA-256, igual que en el GET.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.storage import Block
from app.models.user import User
from app.repositories.block_repository import BlockRepository
from app.services.block_assembler import BlockAssembler, check_complete
from app.services.download_service import STREAM_CHUNK_BYTES
from app.services.handle_service import HandleService
from app.services.temp_storage_service import TempWorkspace, TemporaryStorageService


class InvalidRangeError(Exception):
    """El offset o el length pedidos no son válidos para el archivo."""


@dataclass(frozen=True)
class BlockSlice:
    """Parte de un bloque que entra en el rango pedido."""

    block_index: int
    start: int  # posición dentro del bloque donde empieza
    end: int  # posición dentro del bloque donde termina (sin incluir)


def slices_for_range(blocks: list[Block], offset: int, length: int) -> list[BlockSlice]:
    """Calcula qué pedazo de cada bloque cae dentro de [offset, offset + length).

    Se usan los tamaños reales de los bloques (el último puede estar
    parcialmente lleno), no un tamaño fijo.
    """
    slices: list[BlockSlice] = []
    range_end = offset + length
    block_start = 0
    for block in blocks:
        block_end = block_start + block.used_size_bytes
        if block_end > offset and block_start < range_end:
            slices.append(
                BlockSlice(
                    block_index=block.block_index,
                    start=max(offset, block_start) - block_start,
                    end=min(range_end, block_end) - block_start,
                )
            )
        if block_start >= range_end:
            break
        block_start = block_end
    return slices


@dataclass
class RangeRead:
    """Rango listo para enviarse: partes descifradas en una carpeta temporal."""

    offset: int
    length: int
    slices: list[BlockSlice]
    workspace: TempWorkspace

    def iter_content(self) -> Iterator[bytes]:
        """Envía solo los bytes del rango y limpia los temporales al terminar."""
        try:
            for piece in self.slices:
                with self.workspace.part_path(piece.block_index).open("rb") as part:
                    part.seek(piece.start)
                    remaining = piece.end - piece.start
                    while remaining > 0:
                        chunk = part.read(min(STREAM_CHUNK_BYTES, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
        finally:
            self.close()

    def close(self) -> None:
        """Borra los temporales y libera la reserva."""
        self.workspace.release()


class ReadService:
    """Lee rangos de archivos abiertos con un handle."""

    def __init__(
        self,
        session: Session,
        handles: HandleService,
        temp_storage: TemporaryStorageService,
        max_parallel_downloads: int,
    ) -> None:
        self._handles = handles
        self._blocks = BlockRepository(session)
        self._assembler = BlockAssembler(temp_storage, max_parallel_downloads)

    def read(self, user: User, token: str, offset: int, length: int) -> RangeRead:
        """Prepara la lectura de ``length`` bytes desde ``offset``.

        Si el rango pasa del final del archivo, se recorta al final. Un offset
        mayor que el tamaño del archivo es un error.
        """
        valid = self._handles.validate(user, token)
        self._handles.ensure_can_read(user, valid)
        file = valid.file

        if offset < 0 or length < 0:
            raise InvalidRangeError("offset y length no pueden ser negativos")
        if offset > file.size_bytes:
            raise InvalidRangeError(
                f"el offset {offset} está después del final del archivo ({file.size_bytes} bytes)"
            )
        length = min(length, file.size_bytes - offset)

        blocks = self._blocks.list_for_file_version(file.id, file.current_version)
        check_complete(file, blocks)
        slices = slices_for_range(blocks, offset, length)
        needed = [block for block in blocks if block.block_index in {s.block_index for s in slices}]

        self._handles.touch(valid)
        workspace = self._assembler.reserve(BlockAssembler.bytes_needed(needed))
        try:
            self._assembler.assemble(needed, workspace)
        except BaseException:
            workspace.release()
            raise
        return RangeRead(offset=offset, length=length, slices=slices, workspace=workspace)
