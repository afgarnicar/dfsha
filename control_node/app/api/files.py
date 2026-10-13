"""Endpoints de archivos: subida (PUT, con sobrescritura opcional), descarga (GET) y borrado (rm)."""

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
from app.services.lock_service import FileLockedError
from app.services.path_service import PathNotFoundError, PathPermissionError
from app.services.temp_storage_service import (
    InsufficientTempSpaceError,
    TemporaryStorageService,
)
from app.services.upload_service import (
    FileAlreadyExistsError,
    FileBusyError,
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
_file_locked = HTTPException(
    status_code=status.HTTP_423_LOCKED,
    detail="El archivo está bloqueado: otra sesión lo tiene abierto para escritura.",
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
    overwrite: bool = Form(False),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    """Sube un archivo al directorio indicado, dividiéndolo y replicándolo.

    Con ``overwrite=true`` reemplaza un archivo existente de forma segura: si
    algo falla, la versión anterior sigue intacta.
    """
    settings = get_settings()
    service = UploadService(
        session=session,
        block_size_bytes=settings.block_size_mb * BYTES_PER_MEGABYTE,
        max_retries=settings.max_retries,
        lock_timeout_seconds=settings.lock_timeout_seconds,
    )
    try:
        # El archivo recibido ya está en un temporal del servidor: se lee por
        # bloques desde ahí, sin cargarlo entero en memoria.
        result = service.upload(
            user=current_user,
            directory_path=directory_path,
            filename=file.filename,
            source=file.file,
            overwrite=overwrite,
        )
    except PathNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El directorio de destino no existe.",
        )
    except (PathPermissionError, UploadPermissionError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permiso para escribir en el destino.",
        )
    except FileAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ya existe un archivo con ese nombre en el directorio. "
                "Use overwrite para reemplazarlo."
            ),
        )
    except FileBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El archivo todavía se está subiendo; intente de nuevo en un momento.",
        )
    except FileLockedError:
        raise _file_locked
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
        overwritten=result.overwritten,
    )


@router.get("/download")
def download_file(
    path: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    temp_storage: TemporaryStorageService = Depends(get_temp_storage),
) -> StreamingResponse:
    """Descarga un archivo completo, reconstruido desde sus bloques (GET)."""
    settings = get_settings()
    service = DownloadService(
        session=session,
        temp_storage=temp_storage,
        max_parallel_downloads=settings.max_parallel_downloads,
        lock_timeout_seconds=settings.lock_timeout_seconds,
    )
    try:
        download = service.prepare(current_user, path)
    except (PathNotFoundError, FileNotFoundInPathError):
        raise _file_not_found
    except (PathPermissionError, FileAccessDeniedError):
        raise _file_forbidden
    except FileLockedError:
        raise _file_locked
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
        result = DeleteService(session, get_settings().lock_timeout_seconds).delete(
            current_user, body.path
        )
    except (PathNotFoundError, FileNotFoundInPathError):
        raise _file_not_found
    except (PathPermissionError, FileAccessDeniedError):
        raise _file_forbidden
    except FileLockedError:
        raise _file_locked

    return DeleteFileResponse(
        name=result.name,
        status="deleted" if result.completed else "pending_cleanup",
        pending_replicas=result.pending_replicas,
    )
