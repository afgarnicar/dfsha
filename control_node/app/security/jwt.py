"""Emisión y validación de JSON Web Tokens para la autenticación de usuarios.

Los tokens usan el secreto y el tiempo de expiración definidos en la
configuración. El resto del sistema solo usa ``create_access_token`` y
``decode_token`` sin conocer los detalles de la librería.
"""

from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

# Algoritmo de firma simétrica usado para los tokens de usuario.
_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """Se lanza cuando un token es inválido, está expirado o mal formado."""


def create_access_token(user_id: int, username: str) -> str:
    """Crea un JWT firmado para el usuario indicado.

    El 'subject' del token es el identificador del usuario; se incluye el
    nombre de usuario como dato auxiliar y una fecha de expiración.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_expiration_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Valida y decodifica un JWT, devolviendo su contenido (payload).

    Lanza ``InvalidTokenError`` si el token está expirado o es inválido.
    """
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as error:
        raise InvalidTokenError("Token inválido o expirado.") from error
