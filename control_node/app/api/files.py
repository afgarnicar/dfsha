"""Endpoints de archivos: subida (PUT), descarga (GET) y borrado (rm)."""

from functools import lru_cache
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.models.user import User
from app.schemas.files import DeleteFileRequest, DeleteFileResponse, UploadResponse
from app.security.dependencies import get_current_user
from app.services.block_fetcher import BlockUnavailableError
from app.services.delete_service import DeleteService
from app.services.download_service import (
    DownloadService,
    FileDecryptionError,
    FileIncompleteError,
)
from app.services.file_access_service import FileAccessDeniedError, FileNotFoundInPathError
from app.services.path_service import PathNotFoundError, PathPermissionError
from app.services.temp_storage_service import (
    InsufficientTempSpaceError,
    TemporaryStorageService,
)
from app.services.upload_service import (
    FileAlreadyExistsError,
    UploadFailedError,
    UploadPermissionError,
    UploadService,
)
from shared.constants import BYTES_PER_MEGABYTE

router = APIRouter(prefix="/files", tags=["files"])

# Errores reutilizables para operaciones sobre un archivo existente.
_file_not_found = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="El archivo no existe.",
)
_file_forbidden = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="No tiene permiso para realizar esta operación sobre el archivo.",
)


@lru_cache
def get_temp_storage() -> TemporaryStorageService:
    """Almacenamiento temporal compartido por todos los GET del proceso.

    Es uno solo para que las reservas de espacio de descargas simultáneas se
    tengan en cuenta entre sí.
    """
    settings = get_settings()
    return TemporaryStorageService(settings.temp_root, settings.temp_space_margin_percent)


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_file(
    directory_path: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    """Sube un archivo al directorio indicado, dividiéndolo y replicándolo."""
    settings = get_settings()
    content = file.file.read()

    service = UploadService(
        session=session,
        block_size_bytes=settings.block_size_mb * BYTES_PER_MEGABYTE,
        max_retries=settings.max_retries,
    )
    try:
        result = service.upload(
            user=current_user,
            directory_path=directory_path,
            filename=file.filename,
            content=content,
        )
    except PathNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El directorio de destino no existe.",
        )
    except (PathPermissionError, UploadPermissionError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permiso para escribir en el directorio de destino.",
        )
    except FileAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un archivo con ese nombre en el directorio.",
        )
    except UploadFailedError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        )

    return UploadResponse(
        file_id=result.file_id,
        name=result.name,
        size_bytes=result.size_bytes,
        block_count=result.block_count,
    )


@router.get("/download")
def download_file(
    path: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    temp_storage: TemporaryStorageService = Depends(get_temp_storage),
) -> StreamingResponse:
    """Descarga un archivo completo, reconstruido desde sus bloques (GET)."""
    service = DownloadService(
        session=session,
        temp_storage=temp_storage,
        max_parallel_downloads=get_settings().max_parallel_downloads,
    )
    try:
        download = service.prepare(current_user, path)
    except (PathNotFoundError, FileNotFoundInPathError):
        raise _file_not_found
    except (PathPermissionError, FileAccessDeniedError):
        raise _file_forbidden
    except InsufficientTempSpaceError:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail="No hay espacio temporal suficiente en el ControlNode para la descarga.",
        )
    except BlockUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No fue posible recuperar el archivo: ninguna réplica entregó un "
                f"bloque válido ({error})."
            ),
        )
    except (FileIncompleteError, FileDecryptionError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"El archivo no se puede reconstruir: {error}.",
        )

    return StreamingResponse(
        download.iter_content(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(download.size_bytes),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download.file_name)}",
        },
    )


@router.delete("", response_model=DeleteFileResponse)
def delete_file(
    body: DeleteFileRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DeleteFileResponse:
    """Borra un archivo y sus bloques de los DataNodes (operación rm)."""
    try:
        result = DeleteService(session).delete(current_user, body.path)
    except (PathNotFoundError, FileNotFoundInPathError):
        raise _file_not_found
    except (PathPermissionError, FileAccessDeniedError):
        raise _file_forbidden

    return DeleteFileResponse(
        name=result.name,
        status="deleted" if result.completed else "pending_cleanup",
        pending_replicas=result.pending_replicas,
    )
