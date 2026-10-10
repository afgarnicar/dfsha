"""Esquemas de salida de la API de archivos."""

from pydantic import BaseModel


class UploadResponse(BaseModel):
    """Respuesta a una subida exitosa de archivo."""

    file_id: int
    name: str
    size_bytes: int
    block_count: int
    status: str = "AVAILABLE"
