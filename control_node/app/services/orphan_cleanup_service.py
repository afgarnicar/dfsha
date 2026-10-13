"""Limpieza de copias huérfanas en los DataNodes.

Una copia queda huérfana cuando está en el disco de un DataNode pero los
metadatos ya no la registran. Pasa, por ejemplo, cuando un nodo se cae, sus
bloques se re-replican en otros nodos (y su réplica se quita de los metadatos),
y después el nodo vuelve con las copias viejas todavía en el disco.

Para cada DataNode en línea, este servicio pide la lista de bloques que tiene
guardados (``GET /blocks``) y borra los que no aparecen en los metadatos para
ese nodo.

Solo borra copias con más de ``ORPHAN_GRACE_SECONDS`` de antigüedad. Así no
toca un bloque que se está subiendo o re-replicando justo en ese momento: en
ese caso el bloque ya está en el disco, pero su ubicación todavía no se ha
guardado en los metadatos.
"""

import logging

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.block_repository import BlockRepository
from app.repositories.datanode_repository import DataNodeRepository
from shared.constants import INTERNAL_API_KEY_HEADER
from shared.enums import NodeStatus

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 15.0


class OrphanCleanupService:
    """Borra de los DataNodes las copias que los metadatos ya no registran."""

    def __init__(self, session: Session, grace_seconds: int) -> None:
        self._blocks = BlockRepository(session)
        self._datanodes = DataNodeRepository(session)
        self._grace_seconds = grace_seconds

    def run(self) -> int:
        """Revisa los DataNodes en línea. Devuelve cuántas copias huérfanas borró."""
        removed = 0
        timeout = httpx.Timeout(_REQUEST_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
        headers = {INTERNAL_API_KEY_HEADER: get_settings().internal_api_key}

        with httpx.Client(timeout=timeout, headers=headers) as client:
            for node in self._datanodes.list_all():
                if node.status != NodeStatus.ONLINE:
                    continue
                base = f"http://{node.host}:{node.port}/blocks"
                try:
                    response = client.get(base)
                    response.raise_for_status()
                    stored = response.json()["blocks"]
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    logger.warning(
                        "No se pudo listar los bloques de %s: %s", node.name, type(error).__name__
                    )
                    continue

                known = self._blocks.storage_keys_on_datanode(node.id)
                for block in stored:
                    if block["block_id"] in known or block["age_seconds"] < self._grace_seconds:
                        continue
                    try:
                        deleted = client.delete(f"{base}/{block['block_id']}").status_code == 204
                    except httpx.HTTPError:
                        deleted = False
                    if deleted:
                        removed += 1
                        logger.info(
                            "Copia huérfana %s borrada de %s.", block["block_id"], node.name
                        )
        return removed
