"""Esquemas de entrada y salida de la API para los DataNodes."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from shared.enums import NodeStatus


class DataNodeResponse(BaseModel):
    """Representación pública de un DataNode registrado."""

    # Permite construir el esquema directamente desde el modelo de SQLAlchemy.
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    status: NodeStatus
    free_space_bytes: int
    failure_count: int
    last_check: datetime | None
    created_at: datetime
