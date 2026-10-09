"""Esquemas JSON de entrada y salida de la API del DataNode.

Las claves están en inglés porque son el contrato con el ControlNode.
"""

from pydantic import BaseModel, Field


class StoredBlockResponse(BaseModel):
    """Respuesta al guardar un bloque."""

    block_id: str
    size_bytes: int
    checksum: str


class ReplicateRequest(BaseModel):
    """DataNode destino al que se debe copiar un bloque."""

    target_host: str = Field(min_length=1, max_length=255)
    target_port: int = Field(ge=1, le=65535)


class ReplicateResponse(BaseModel):
    """Resultado de copiar un bloque a otro DataNode."""

    block_id: str
    target: str
    size_bytes: int
    checksum: str
