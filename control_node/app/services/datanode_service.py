"""Servicio de registro de DataNodes.

Siembra en la base de datos los DataNodes declarados en la configuración. La
operación es idempotente: si un nodo ya existe, actualiza su endpoint en lugar
de duplicarlo. No modifica el estado de disponibilidad de los nodos existentes,
para no interferir con el monitor de nodos.
"""

import logging
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.config import DataNodeConfig
from app.models.storage import DataNode
from app.repositories.datanode_repository import DataNodeRepository

logger = logging.getLogger(__name__)


class DataNodeRegistryService:
    """Registra los DataNodes configurados en la base de datos de metadatos."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = DataNodeRepository(session)

    def sync_from_config(
        self, configured_nodes: Sequence[DataNodeConfig]
    ) -> list[DataNode]:
        """Sincroniza la tabla de DataNodes con la lista de la configuración.

        Por cada nodo configurado: si no existe, lo crea en estado OFFLINE; si
        existe, actualiza su host y puerto. Devuelve la lista resultante de
        DataNodes afectados.
        """
        affected: list[DataNode] = []
        for node in configured_nodes:
            existing = self._repository.get_by_name(node.node_id)
            if existing is None:
                created = self._repository.create(node.node_id, node.host, node.port)
                affected.append(created)
                logger.info("DataNode registrado: %s (%s:%d)", node.node_id, node.host, node.port)
            else:
                updated = self._repository.update_endpoint(existing, node.host, node.port)
                affected.append(updated)
                logger.info("DataNode actualizado: %s (%s:%d)", node.node_id, node.host, node.port)

        self._session.commit()
        return affected

    def list_datanodes(self) -> Sequence[DataNode]:
        """Devuelve todos los DataNodes registrados."""
        return self._repository.list_all()
