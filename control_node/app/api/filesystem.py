"""Endpoints del sistema de archivos lógico: directorios y listado.

Todas las operaciones requieren autenticación y validan los permisos del
usuario sobre los directorios involucrados.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.user import User
from app.schemas.filesystem import (
    CreateDirectoryRequest,
    DirectoryListingResponse,
    DirectoryResponse,
    FileSummary,
    RemoveDirectoryRequest,
)
from app.security.dependencies import get_current_user
from app.services.directory_service import (
    CannotRemoveRootError,
    DirectoryAlreadyExistsError,
    DirectoryNotEmptyError,
    DirectoryService,
    InvalidPathError,
    PermissionDeniedError,
)
from app.services.path_service import PathNotFoundError, PathPermissionError

router = APIRouter(tags=["filesystem"])

# Error reutilizable cuando falta permiso sobre un directorio.
_forbidden = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="No tiene permiso para realizar esta operación.",
)
# Error reutilizable cuando la ruta no existe.
_not_found = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="La ruta indicada no existe.",
)


@router.post(
    "/directories", response_model=DirectoryResponse, status_code=status.HTTP_201_CREATED
)
def create_directory(
    body: CreateDirectoryRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DirectoryResponse:
    """Crea un directorio en la ruta indicada (operación mkdir)."""
    service = DirectoryService(session)
    try:
        directory = service.make_directory(current_user, body.path, body.permissions)
    except (PathNotFoundError, InvalidPathError):
        raise _not_found
    except PathPermissionError:
        raise _forbidden
    except PermissionDeniedError:
        raise _forbidden
    except DirectoryAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un directorio con ese nombre.",
        )
    return DirectoryResponse.model_validate(directory)


@router.delete("/directories", status_code=status.HTTP_204_NO_CONTENT)
def remove_directory(
    body: RemoveDirectoryRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Elimina un directorio vacío (operación rmdir)."""
    service = DirectoryService(session)
    try:
        service.remove_directory(current_user, body.path)
    except (PathNotFoundError, CannotRemoveRootError):
        raise _not_found
    except PathPermissionError:
        raise _forbidden
    except PermissionDeniedError:
        raise _forbidden
    except DirectoryNotEmptyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El directorio no está vacío.",
        )


@router.get("/filesystem/list", response_model=DirectoryListingResponse)
def list_directory(
    path: str = Query(default="/"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DirectoryListingResponse:
    """Lista el contenido de un directorio (operación ls)."""
    service = DirectoryService(session)
    try:
        listing = service.list_directory(current_user, path)
    except PathNotFoundError:
        raise _not_found
    except (PathPermissionError, PermissionDeniedError):
        raise _forbidden
    return DirectoryListingResponse(
        path=path,
        directory_id=listing.directory.id,
        subdirectories=[
            DirectoryResponse.model_validate(child) for child in listing.subdirectories
        ],
        files=[FileSummary.model_validate(f) for f in listing.files],
    )


@router.get("/filesystem/resolve", response_model=DirectoryResponse)
def resolve_directory(
    path: str = Query(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DirectoryResponse:
    """Valida que el usuario puede entrar a un directorio (operación cd)."""
    service = DirectoryService(session)
    try:
        directory = service.resolve_directory(current_user, path)
    except PathNotFoundError:
        raise _not_found
    except (PathPermissionError, PermissionDeniedError):
        raise _forbidden
    return DirectoryResponse.model_validate(directory)
