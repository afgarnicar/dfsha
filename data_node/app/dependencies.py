"""Dependencias compartidas de la API del DataNode.

Los endpoints reciben el almacén de bloques y el cliente de replicación por
inyección de dependencias de FastAPI, en lugar de crearlos por su cuenta. Así
cada pieza se puede reemplazar fácilmente en las pruebas.
"""

from functools import lru_cache

from fastapi import Depends

from app.config import get_settings
from app.services.replication_client import ReplicationClient
from app.storage.block_store import BlockStore


@lru_cache
def get_block_store() -> BlockStore:
    """Almacén de bloques de este nodo, apuntando a STORAGE_ROOT."""
    return BlockStore(get_settings().storage_root)


def get_replication_client(
    store: BlockStore = Depends(get_block_store),
) -> ReplicationClient:
    """Cliente para copiar bloques de este nodo hacia otros DataNodes."""
    return ReplicationClient(store)
