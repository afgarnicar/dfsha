"""Dependencias de seguridad para proteger endpoints.

Proporciona ``get_current_user``, que extrae y valida el JWT del encabezado
Authorization y devuelve el usuario autenticado. Los endpoints que requieren
autenticación declaran esta dependencia.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.security.jwt import InvalidTokenError, decode_token

# Esquema de seguridad Bearer. Con auto_error=False manejamos nosotros el caso
# de credenciales ausentes, para devolver 401 y un mensaje en español en lugar
# del 403 en inglés que produce FastAPI por defecto.
_bearer_scheme = HTTPBearer(auto_error=False)

# Error reutilizable para credenciales inválidas o ausentes.
_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="No autenticado: token inválido o ausente.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Devuelve el usuario autenticado a partir del token Bearer.

    Lanza 401 si no se envió el token, es inválido, está expirado o el usuario
    ya no existe o está inactivo.
    """
    if credentials is None:
        raise _credentials_error

    try:
        payload = decode_token(credentials.credentials)
    except InvalidTokenError as error:
        raise _credentials_error from error

    subject = payload.get("sub")
    if subject is None:
        raise _credentials_error

    user = UserRepository(session).get_by_id(int(subject))
    if user is None or not user.is_active:
        raise _credentials_error

    return user
