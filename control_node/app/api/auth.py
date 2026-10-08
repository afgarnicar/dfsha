"""Endpoints de autenticación: registro, inicio de sesión y usuario actual."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.security.dependencies import get_current_user
from app.services.auth_service import (
    AuthService,
    InvalidCredentialsError,
    UsernameAlreadyExistsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest, session: Session = Depends(get_session)
) -> UserResponse:
    """Registra un usuario nuevo y devuelve sus datos públicos."""
    service = AuthService(session)
    try:
        user = service.register(body.username, body.password)
    except UsernameAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El nombre de usuario ya está en uso.",
        )
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest, session: Session = Depends(get_session)
) -> TokenResponse:
    """Valida las credenciales y devuelve un token de acceso."""
    service = AuthService(session)
    try:
        token = service.authenticate(body.username, body.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas.",
        )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Devuelve los datos del usuario autenticado (endpoint protegido)."""
    return UserResponse.model_validate(current_user)
