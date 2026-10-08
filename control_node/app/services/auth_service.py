"""Servicio de autenticación de usuarios.

Contiene la lógica de registro e inicio de sesión. Lanza excepciones de dominio
propias que la capa de API traduce a códigos HTTP, de modo que el servicio no
dependa de FastAPI.
"""

from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.security.jwt import create_access_token
from app.security.password import hash_password, verify_password
from app.services.home_service import HomeDirectoryService
from sqlalchemy.orm import Session


class UsernameAlreadyExistsError(Exception):
    """Se lanza al intentar registrar un nombre de usuario ya existente."""


class InvalidCredentialsError(Exception):
    """Se lanza cuando el usuario o la contraseña del login son incorrectos."""


class AuthService:
    """Casos de uso de registro y autenticación de usuarios."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = UserRepository(session)

    def register(self, username: str, password: str) -> User:
        """Registra un usuario nuevo.

        Lanza ``UsernameAlreadyExistsError`` si el nombre ya está en uso. La
        contraseña se almacena únicamente como hash Argon2.
        """
        if self._repository.get_by_username(username) is not None:
            raise UsernameAlreadyExistsError(username)

        user = self._repository.create(username, hash_password(password))
        self._session.commit()
        self._session.refresh(user)

        # Crea la carpeta personal del usuario recién registrado.
        HomeDirectoryService(self._session).ensure_home_for(user)
        return user

    def authenticate(self, username: str, password: str) -> str:
        """Valida las credenciales y devuelve un token de acceso.

        Lanza ``InvalidCredentialsError`` si el usuario no existe, está inactivo
        o la contraseña no coincide. El mensaje es genérico a propósito, para no
        revelar qué nombres de usuario existen.
        """
        user = self._repository.get_by_username(username)
        if user is None or not user.is_active:
            raise InvalidCredentialsError()
        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        return create_access_token(user.id, user.username)
