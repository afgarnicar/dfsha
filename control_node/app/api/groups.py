"""Endpoints de gestión de grupos y membresías.

Todos los endpoints requieren autenticación. Por ahora cualquier usuario
autenticado puede crear grupos y agregar miembros.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.user import User
from app.schemas.group import AddMemberRequest, CreateGroupRequest, GroupResponse
from app.security.dependencies import get_current_user
from app.services.group_service import (
    GroupAlreadyExistsError,
    GroupNotFoundError,
    GroupService,
    MemberUserNotFoundError,
)

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    body: CreateGroupRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GroupResponse:
    """Crea un grupo nuevo."""
    service = GroupService(session)
    try:
        group = service.create_group(body.name)
    except GroupAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El nombre del grupo ya está en uso.",
        )
    return GroupResponse.model_validate(group)


@router.get("", response_model=list[GroupResponse])
def list_groups(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[GroupResponse]:
    """Lista todos los grupos registrados."""
    service = GroupService(session)
    return [GroupResponse.model_validate(group) for group in service.list_groups()]


@router.post("/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(
    group_id: int,
    body: AddMemberRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Agrega un usuario a un grupo."""
    service = GroupService(session)
    try:
        service.add_member(group_id, body.user_id)
    except GroupNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El grupo indicado no existe.",
        )
    except MemberUserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El usuario indicado no existe.",
        )
