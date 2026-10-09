"""Endpoints de bloques del DataNode: guardar, leer, borrar y replicar.

El DataNode trata cada bloque como contenido opaco identificado por un
``block_id`` que genera el ControlNode. No cifra, no interpreta rutas lógicas y
no decide nada sobre la distribución.

Los endpoints son funciones normales (no ``async``) a propósito: hacen lectura
y escritura de disco, que es bloqueante, y FastAPI las ejecuta en un hilo
aparte para no frenar las demás peticiones.
"""

import errno

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.dependencies import get_block_store, get_replication_client
from app.schemas import ReplicateRequest, ReplicateResponse, StoredBlockResponse
from app.services.replication_client import ReplicationClient, ReplicationTargetError
from app.storage.block_store import (
    BlockNotFoundError,
    BlockStore,
    ChecksumMismatchError,
    InvalidBlockIdError,
)
from app.storage.checksum import is_valid_sha256_hex

router = APIRouter(prefix="/blocks", tags=["blocks"])

# Errores reutilizables, con mensajes en español.
_invalid_block_id = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail=(
        "El block_id no es válido: use solo letras, números, punto, guion o guion "
        "bajo, empezando por letra o número (máximo 128 caracteres)."
    ),
)
_block_not_found = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="El bloque no existe en este DataNode.",
)


def _disk_error(error: OSError) -> HTTPException:
    """Traduce un error de disco a una respuesta HTTP con mensaje claro."""
    if error.errno == errno.ENOSPC:
        return HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="El DataNode no tiene espacio suficiente para guardar el bloque.",
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Error de disco en el DataNode: {error.strerror or error}.",
    )


@router.post("", response_model=StoredBlockResponse, status_code=status.HTTP_201_CREATED)
def store_block(
    block_id: str = Form(...),
    checksum: str = Form(...),
    file: UploadFile = File(...),
    store: BlockStore = Depends(get_block_store),
) -> StoredBlockResponse:
    """Guarda un bloque después de verificar su SHA-256.

    Si ya existe un bloque con ese id, se reemplaza (un reintento del
    ControlNode con el mismo id deja el mismo resultado).
    """
    if not is_valid_sha256_hex(checksum):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El checksum debe ser un SHA-256 en hexadecimal (64 caracteres).",
        )
    try:
        stored = store.save(block_id, file.file, checksum)
    except InvalidBlockIdError:
        raise _invalid_block_id
    except ChecksumMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "El checksum no coincide con el contenido recibido; el bloque no se "
                f"guardó. Esperado: {error.expected}. Calculado: {error.actual}."
            ),
        )
    except OSError as error:
        raise _disk_error(error)
    finally:
        file.file.close()

    return StoredBlockResponse(
        block_id=stored.block_id, size_bytes=stored.size_bytes, checksum=stored.checksum
    )


@router.get("/{block_id}")
def read_block(block_id: str, store: BlockStore = Depends(get_block_store)) -> StreamingResponse:
    """Devuelve el contenido de un bloque en streaming, sin cargarlo entero en memoria.

    La verificación del checksum la hace el ControlNode, que es quien guarda el
    SHA-256 esperado de cada bloque en PostgreSQL.
    """
    try:
        size = store.size_of(block_id)
        chunks = store.iter_chunks(block_id)
    except InvalidBlockIdError:
        raise _invalid_block_id
    except BlockNotFoundError:
        raise _block_not_found
    except OSError as error:
        raise _disk_error(error)

    return StreamingResponse(
        chunks,
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


@router.delete("/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_block(block_id: str, store: BlockStore = Depends(get_block_store)) -> Response:
    """Borra un bloque. Es idempotente: responde 204 aunque el bloque ya no exista.

    Así el ControlNode puede reintentar la limpieza (por ejemplo, después de que
    un nodo vuelva de estar OFFLINE) sin tratar como error un bloque ya borrado.
    """
    try:
        store.delete(block_id)
    except InvalidBlockIdError:
        raise _invalid_block_id
    except OSError as error:
        raise _disk_error(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{block_id}/replicate", response_model=ReplicateResponse)
def replicate_block(
    block_id: str,
    body: ReplicateRequest,
    replicator: ReplicationClient = Depends(get_replication_client),
) -> ReplicateResponse:
    """Copia un bloque local hacia otro DataNode (lo ordena el ControlNode)."""
    try:
        result = replicator.replicate(block_id, body.target_host, body.target_port)
    except InvalidBlockIdError:
        raise _invalid_block_id
    except BlockNotFoundError:
        raise _block_not_found
    except ReplicationTargetError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error))
    except OSError as error:
        raise _disk_error(error)

    return ReplicateResponse(
        block_id=result.block_id,
        target=result.target,
        size_bytes=result.size_bytes,
        checksum=result.checksum,
    )
