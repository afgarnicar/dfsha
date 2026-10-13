"""Cliente para ordenar la copia de un bloque entre DataNodes.

El ControlNode no mueve los bytes de las réplicas: le ordena a un DataNode que
ya tiene una copia válida que la envíe a otro DataNode. Para eso usa el endpoint
``POST /blocks/{storage_key}/replicate`` del nodo origen, indicándole el destino.
Los bytes viajan directamente de DataNode a DataNode.
"""

import logging

import httpx

from app.config import get_settings
from app.services import datanode_http
from shared.constants import INTERNAL_API_KEY_HEADER

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5.0
_TRANSFER_TIMEOUT_SECONDS = 120.0


class ReplicationOrderError(Exception):
    """El DataNode origen no pudo copiar el bloque al destino."""


class ReplicationOrderClient:
    """Ordena a un DataNode copiar un bloque hacia otro DataNode."""

    def order_replication(
        self,
        source_host: str,
        source_port: int,
        storage_key: str,
        target_host: str,
        target_port: int,
    ) -> None:
        """Pide al nodo origen que copie el bloque ``storage_key`` al nodo destino.

        Lanza ``ReplicationOrderError`` si la orden no se completa con éxito.
        """
        url = datanode_http.url(
            source_host, source_port, f"/blocks/{storage_key}/replicate"
        )
        timeout = httpx.Timeout(
            _TRANSFER_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS
        )
        try:
            with httpx.Client(timeout=timeout, verify=datanode_http.verify()) as client:
                response = client.post(
                    url,
                    json={"target_host": target_host, "target_port": target_port},
                    headers={INTERNAL_API_KEY_HEADER: get_settings().internal_api_key},
                )
        except httpx.HTTPError as error:
            raise ReplicationOrderError(
                f"No fue posible contactar al DataNode origen {source_host}:{source_port}: "
                f"{type(error).__name__}."
            ) from error

        if response.status_code not in (200, 201):
            raise ReplicationOrderError(
                f"El DataNode {source_host}:{source_port} no pudo copiar el bloque "
                f"{storage_key} a {target_host}:{target_port} "
                f"(HTTP {response.status_code}): {response.text[:200]}"
            )
