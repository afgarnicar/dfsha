"""Endpoints de acceso a archivos: open, read, write y close.

- ``POST /files/open``  -> abre un archivo y entrega un handle.
- ``POST /files/read``  -> lee un rango (offset, length) del archivo abierto.
- ``POST /files/write`` -> escribe datos desde un offset (multipart/form-data).
- ``POST /files/close`` -> cierra el handle y libera el lock si era de escritura.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.files import get_temp_storage
from app.config import get_settings
from app.db.session import get_session
from app.models.user import User
from app.schemas.file_sessions import (
    CloseRequest,
    OpenRequest,
    OpenResponse,
    ReadRequest,
    WriteResponse,
)
from app.security.dependencies import get_current_user
from app.services.block_assembler import FileDecryptionError, FileIncompleteError
from app.services.block_fetcher import BlockUnavailableError
from app.services.file_access_service import FileAccessDeniedError, FileNotFoundInPathError
from app.services.handle_service import HandleInvalidError, HandleModeError, HandleService
from app.services.lock_service import FileLockedError
from app.services.path_service import PathNotFoundError, PathPermissionError
from app.services.read_service import InvalidRangeError, ReadService
from app.services.temp_storage_service import (
    InsufficientTempSpaceError,
    TemporaryStorageService,
)
from app.services.write_service import WriteFailedError, WriteService

router = APIRouter(prefix="/files", tags=["file-sessions"])


def get_handle_service(session: Session = Depends(get_session)) -> HandleService:
    """Servicio de handles con los tiempos de la configuración."""
    settings = get_settings()
    return HandleService(
        session, settings.lock_timeout_seconds, settings.handle_timeout_seconds
    )


def _http_error(error: Exception) -> HTTPException:
    """Traduce los errores de las operaciones a respuestas HTTP en español."""
    if isinstance(error, (PathNotFoundError, FileNotFoundInPathError)):
        return HTTPException(status.HTTP_404_NOT_FOUND, "El archivo no existe.")
    if isinstance(error, (PathPermissionError, FileAccessDeniedError)):
        return HTTPException(
            status.HTTP_403_FORBIDDEN,
            "No tiene permiso para realizar esta operación sobre el archivo.",
        )
    if isinstance(error, FileLockedError):
        return HTTPException(
            status.HTTP_423_LOCKED,
            "El archivo está bloqueado: otra sesión lo tiene abierto para escritura.",
        )
    if isinstance(error, HandleInvalidError):
        return HTTPException(
            status.HTTP_410_GONE,
            "El handle no es válido: está cerrado, venció o no le pertenece.",
        )
    if isinstance(error, HandleModeError):
        return HTTPException(
            status.HTTP_400_BAD_REQUEST, "El archivo se abrió solo para lectura."
        )
    if isinstance(error, InvalidRangeError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, f"Rango inválido: {error}.")
    if isinstance(error, InsufficientTempSpaceError):
        return HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE,
            "No hay espacio temporal suficiente en el ControlNode.",
        )
    if isinstance(error, BlockUnavailableError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"No fue posible recuperar el archivo: ninguna réplica entregó un bloque válido ({error}).",
        )
    if isinstance(error, WriteFailedError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"La escritura no se completó y el archivo quedó como estaba: {error}.",
        )
    if isinstance(error, (FileIncompleteError, FileDecryptionError)):
        return HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"El archivo no se puede reconstruir: {error}.",
        )
    raise error


_HANDLED_ERRORS = (
    PathNotFoundError,
    FileNotFoundInPathError,
    PathPermissionError,
    FileAccessDeniedError,
    FileLockedError,
    HandleInvalidError,
    HandleModeError,
    InvalidRangeError,
    InsufficientTempSpaceError,
    BlockUnavailableError,
    WriteFailedError,
    FileIncompleteError,
    FileDecryptionError,
)


@router.post("/open", response_model=OpenResponse)
def open_file(
    body: OpenRequest,
    current_user: User = Depends(get_current_user),
    handles: HandleService = Depends(get_handle_service),
) -> OpenResponse:
    """Abre un archivo en modo READ o WRITE y devuelve su handle."""
    try:
        opened = handles.open(current_user, body.path, body.mode)
    except _HANDLED_ERRORS as error:
        raise _http_error(error)
    return OpenResponse(
        handle=opened.token,
        mode=opened.mode,
        name=opened.file_name,
        size_bytes=opened.size_bytes,
        expires_at=opened.expires_at,
    )


@router.post("/read")
def read_file(
    body: ReadRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    handles: HandleService = Depends(get_handle_service),
    temp_storage: TemporaryStorageService = Depends(get_temp_storage),
) -> StreamingResponse:
    """Devuelve los bytes del rango pedido del archivo abierto."""
    service = ReadService(
        session, handles, temp_storage, get_settings().max_parallel_downloads
    )
    try:
        result = service.read(current_user, body.handle, body.offset, body.length)
    except _HANDLED_ERRORS as error:
        raise _http_error(error)
    return StreamingResponse(
        result.iter_content(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(result.length),
            "X-DFSHA-Offset": str(result.offset),
        },
    )


@router.post("/write", response_model=WriteResponse)
def write_file(
    handle: str = Form(..., min_length=1),
    offset: int = Form(..., ge=0),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    handles: HandleService = Depends(get_handle_service),
    temp_storage: TemporaryStorageService = Depends(get_temp_storage),
) -> WriteResponse:
    """Escribe el contenido recibido desde ``offset`` (reemplazo seguro del archivo)."""
    settings = get_settings()
    service = WriteService(
        session=session,
        handles=handles,
        temp_storage=temp_storage,
        max_parallel_downloads=settings.max_parallel_downloads,
        block_size_bytes=settings.block_size_bytes,
        max_retries=settings.max_retries,
    )
    try:
        result = service.write(current_user, handle, offset, file.file)
    except _HANDLED_ERRORS as error:
        raise _http_error(error)
    finally:
        file.file.close()
    return WriteResponse(
        size_bytes=result.size_bytes,
        version=result.version,
        block_count=result.block_count,
        expires_at=result.expires_at,
    )


@router.post("/close", status_code=status.HTTP_204_NO_CONTENT)
def close_file(
    body: CloseRequest,
    current_user: User = Depends(get_current_user),
    handles: HandleService = Depends(get_handle_service),
) -> Response:
    """Cierra el handle. Si era de escritura, libera el lock del archivo."""
    try:
        handles.close(current_user, body.handle)
    except _HANDLED_ERRORS as error:
        raise _http_error(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
