"""Endpoint de salud del DataNode.

El ControlNode lo consulta periódicamente (monitor de nodos) para saber si el
nodo está vivo y cuánto espacio libre tiene. Las claves del JSON están en
inglés porque son parte del contrato entre nodos.
"""

from fastapi import APIRouter

from app.config import get_settings
from app.storage.disk_usage import get_free_space_bytes

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Reporta identidad, estado y espacio libre del nodo."""
    settings = get_settings()
    return {
        "node_id": settings.node_id,
        "status": "ok",
        "free_space_bytes": get_free_space_bytes(settings.storage_root),
    }
