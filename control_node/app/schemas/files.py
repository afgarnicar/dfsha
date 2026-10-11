"""Esquemas de entrada y salida de la API de archivos."""

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """Respuesta a una subida exitosa de archivo."""

    file_id: int
    name: str
    size_bytes: int
    block_count: int
    status: str = "AVAILABLE"


class DeleteFileRequest(BaseModel):
    """Archivo a borrar, por su ruta lógica completa."""

    path: str = Field(min_length=1)


class DeleteFileResponse(BaseModel):
    """Resultado de borrar un archivo.

    ``status`` vale ``deleted`` si todas las réplicas se borraron, o
    ``pending_cleanup`` si algún DataNode no respondió: el archivo ya no es
    visible y sus réplicas restantes se borran cuando el nodo vuelva.
    """

    name: str
    status: str
    pending_replicas: int
