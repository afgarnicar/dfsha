"""Borrado de réplicas de bloques en los DataNodes.

Pide a cada DataNode que borre su copia de un bloque (``DELETE /blocks/{id}``)
y quita de los metadatos las réplicas que sí se borraron. Las que no se
pudieron borrar (por ejemplo, porque el nodo está OFFLINE) se dejan registradas
para reintentar después.

Lo usan el rm, la limpieza periódica y el write (para borrar la versión vieja
de un archivo o la versión nueva que falló a medias).
"""

import logging

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.storage import Block
from app.repositories.block_repository import BlockRepository
from shared.constants import INTERNAL_API_KEY_HEADER

logger = logging.getLogger(__name__)

# Tiempos de espera para borrar un bloque: es una operación pequeña.
_CONNECT_TIMEOUT_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 10.0


class ReplicaEraser:
    """Borra réplicas de bloques en los DataNodes y actualiza los metadatos."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._blocks = BlockRepository(session)

    def erase(self, blocks: list[Block], drop_empty_blocks: bool = False) -> int:
        """Borra las réplicas de los bloques. Devuelve cuántas quedaron pendientes.

        Con ``drop_empty_blocks``, un bloque que se queda sin réplicas también se
        borra de los metadatos (se usa al limpiar versiones viejas).
        """
        pending = 0
        timeout = httpx.Timeout(_REQUEST_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
        headers = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}

        with httpx.Client(timeout=timeout, headers=headers) as client:
            for block in blocks:
                for location in list(block.locations):
                    node = location.datanode
                    url = f"http://{node.host}:{node.port}/blocks/{block.storage_key}"
                    try:
                        response = client.delete(url)
                        deleted = response.status_code == 204
                        reason = f"HTTP {response.status_code}"
                    except httpx.HTTPError as error:
                        deleted = False
                        reason = type(error).__name__

                    if deleted:
                        self._blocks.delete_location(location)
                    else:
                        pending += 1
                        logger.warning(
                            "No se pudo borrar el bloque %s en %s (%s); queda pendiente.",
                            block.storage_key, node.name, reason,
                        )

                if drop_empty_blocks and not block.locations:
                    self._blocks.delete_block(block)
                # Se confirma por bloque para no perder el progreso si algo falla.
                self._session.commit()

        return pending
