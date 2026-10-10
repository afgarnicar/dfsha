"""Endpoints de archivos: subida (PUT) de un archivo al sistema distribuido."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.models.user import User
from app.schemas.files import UploadResponse
from app.security.dependencies import get_current_user
from app.services.path_service import PathNotFoundError, PathPermissionError
from app.services.upload_service import (
    FileAlreadyExistsError,
    UploadFailedError,
    UploadPermissionError,
    UploadService,
)
from shared.constants import BYTES_PER_MEGABYTE

router = APIRouter(prefix="/files", tags=["files"])


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
