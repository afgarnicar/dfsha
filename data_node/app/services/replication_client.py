"""Envío de un bloque de este DataNode hacia otro DataNode.

Se usa en la re-replicación: el ControlNode le ordena a un DataNode que tiene
una copia válida que se la pase a otro. Los bytes viajan directamente de
DataNode a DataNode, sin pasar por el ControlNode.

Para enviarlo se reutiliza el mismo contrato de ``POST /blocks`` del destino:
``block_id``, ``checksum`` y el contenido en ``multipart/form-data``.
"""

from dataclasses import dataclass

import httpx

from app.storage.block_store import BlockNotFoundError, BlockStore
from app.storage.checksum import file_sha256

# Tiempo máximo para conectar con el destino.
CONNECT_TIMEOUT_SECONDS = 5.0
# Tiempo máximo de espera por cada lectura o escritura. Es amplio porque un
# bloque puede pesar 64 MB.
TRANSFER_TIMEOUT_SECONDS = 120.0


class ReplicationTargetError(RuntimeError):
    """El DataNode destino no respondió o rechazó el bloque."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ReplicationResult:
    """Resultado de copiar un bloque a otro DataNode."""

    block_id: str
    target: str
    size_bytes: int
    checksum: str


class ReplicationClient:
    """Copia bloques locales hacia otros DataNodes por HTTP."""

    def __init__(self, store: BlockStore) -> None:
        self._store = store

    def replicate(self, block_id: str, target_host: str, target_port: int) -> ReplicationResult:
        """Envía el bloque local ``block_id`` al DataNode ``target_host:target_port``.

        El checksum se recalcula del archivo en disco justo antes de enviarlo, así
        el destino detecta si el bloque se dañó en el camino.
        """
        path = self._store.path_for(block_id)
        if not path.is_file():
            raise BlockNotFoundError(block_id)

        checksum = file_sha256(path)
        target = f"{target_host}:{target_port}"
        url = f"http://{target}/blocks"
        timeout = httpx.Timeout(TRANSFER_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)

        try:
            # httpx lee el archivo por partes al armar el multipart, sin cargarlo
            # completo en memoria.
            with path.open("rb") as content, httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    data={"block_id": block_id, "checksum": checksum},
                    files={"file": (block_id, content, "application/octet-stream")},
                )
        except httpx.HTTPError as error:
            raise ReplicationTargetError(
                f"No fue posible contactar al DataNode destino {target}: "
                f"{type(error).__name__}."
            ) from error

        if response.status_code != 201:
            raise ReplicationTargetError(
                f"El DataNode destino {target} rechazó el bloque "
                f"(HTTP {response.status_code}): {response.text[:200]}",
                status_code=response.status_code,
            )

        body = response.json()
        return ReplicationResult(
            block_id=block_id,
            target=target,
            size_bytes=int(body.get("size_bytes", 0)),
            checksum=checksum,
        )
