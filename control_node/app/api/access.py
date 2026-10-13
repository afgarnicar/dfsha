"""Endpoints de permisos: consultar y cambiar permisos, grupo y ACL.

- ``GET    /access?path=...``          -> permisos, propietario, grupo y ACL.
- ``PATCH  /access/mode``              -> chmod (solo el propietario).
- ``PATCH  /access/group``             -> chgrp (solo el propietario).
- ``PUT    /access/acl``               -> crea o reemplaza una entrada de ACL.
- ``DELETE /access/acl``               -> quita una entrada de ACL.
- ``POST   /access/groups``            -> crea un grupo (el creador queda como miembro).
- ``POST   /access/groups/{name}/members`` -> agrega un usuario a un grupo, por nombre.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.user import User
from app.schemas.access import (
    AclEntryResponse,
    AclRemoveRequest,
    AclSetRequest,
    ChgrpRequest,
    ChmodRequest,
    GroupCreateRequest,
    GroupMemberRequest,
    ResourceInfoResponse,
)
from app.security.dependencies import get_current_user
from app.services.access_control_service import (
    AccessControlService,
    AclEntryNotFoundError,
    GroupNameTakenError,
    NotGroupMemberError,
    NotOwnerError,
    ResourceInfo,
    SubjectNotFoundError,
    mode_string,
)
from app.services.path_service import PathNotFoundError, PathPermissionError

router = APIRouter(prefix="/access", tags=["access"])

_HANDLED = (
    PathNotFoundError,
    PathPermissionError,
    NotOwnerError,
    SubjectNotFoundError,
    NotGroupMemberError,
    AclEntryNotFoundError,
    GroupNameTakenError,
)


def _http_error(error: Exception) -> HTTPException:
    """Traduce los errores de permisos a respuestas HTTP en español."""
    if isinstance(error, PathNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "La ruta indicada no existe.")
    if isinstance(error, PathPermissionError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN, "No tiene permiso para llegar a esa ruta."
        )
    if isinstance(error, NotOwnerError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo el propietario puede cambiar los permisos de este recurso.",
        )
    if isinstance(error, SubjectNotFoundError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, f"El usuario o grupo '{error}' no existe."
        )
    if isinstance(error, NotGroupMemberError):
        return HTTPException(
            status.HTTP_403_FORBIDDEN, f"No pertenece al grupo '{error}'."
        )
    if isinstance(error, AclEntryNotFoundError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, f"No hay una entrada de ACL para '{error}'."
        )
    if isinstance(error, GroupNameTakenError):
        return HTTPException(
            status.HTTP_409_CONFLICT, "El nombre del grupo ya está en uso."
        )
    raise error


def _response(info: ResourceInfo) -> ResourceInfoResponse:
    return ResourceInfoResponse(
        path=info.path,
        type=info.resource_type,
        owner=info.owner,
        group=info.group,
        mode=f"{info.permissions:03o}",
        mode_string=mode_string(info.permissions),
        acl=[
            AclEntryResponse(
                subject_type=entry.subject_type,
                subject=entry.subject,
                read=entry.can_read,
                write=entry.can_write,
                execute=entry.can_execute,
            )
            for entry in info.acl
        ],
    )


@router.get("", response_model=ResourceInfoResponse)
def get_access(
    path: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ResourceInfoResponse:
    """Muestra propietario, grupo, permisos y ACL de un archivo o directorio."""
    try:
        return _response(AccessControlService(session).info(current_user, path))
    except _HANDLED as error:
        raise _http_error(error)


@router.patch("/mode", response_model=ResourceInfoResponse)
def change_mode(
    body: ChmodRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ResourceInfoResponse:
    """Cambia los permisos rwx (chmod). Solo el propietario."""
    try:
        info = AccessControlService(session).chmod(current_user, body.path, int(body.mode, 8))
    except _HANDLED as error:
        raise _http_error(error)
    return _response(info)


@router.patch("/group", response_model=ResourceInfoResponse)
def change_group(
    body: ChgrpRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ResourceInfoResponse:
    """Asocia el recurso a un grupo (chgrp). Solo el propietario."""
    try:
        info = AccessControlService(session).chgrp(current_user, body.path, body.group)
    except _HANDLED as error:
        raise _http_error(error)
    return _response(info)


@router.put("/acl", response_model=ResourceInfoResponse)
def set_acl(
    body: AclSetRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ResourceInfoResponse:
    """Crea o reemplaza la ACL de un usuario o grupo sobre el recurso."""
    try:
        info = AccessControlService(session).set_acl(
            current_user, body.path, body.subject_type, body.subject,
            body.read, body.write, body.execute,
        )
    except _HANDLED as error:
        raise _http_error(error)
    return _response(info)


@router.delete("/acl", response_model=ResourceInfoResponse)
def remove_acl(
    body: AclRemoveRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ResourceInfoResponse:
    """Quita la ACL de un usuario o grupo sobre el recurso."""
    try:
        info = AccessControlService(session).remove_acl(
            current_user, body.path, body.subject_type, body.subject
        )
    except _HANDLED as error:
        raise _http_error(error)
    return _response(info)


@router.post("/groups", status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Crea un grupo; quien lo crea queda como su primer miembro."""
    try:
        AccessControlService(session).create_group(current_user, body.name)
    except _HANDLED as error:
        raise _http_error(error)
    return {"group": body.name, "members": [current_user.username]}


@router.post("/groups/{group_name}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_group_member(
    group_name: str,
    body: GroupMemberRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Agrega un usuario a un grupo. Solo un miembro del grupo puede hacerlo."""
    try:
        AccessControlService(session).add_group_member(current_user, group_name, body.username)
    except _HANDLED as error:
        raise _http_error(error)
