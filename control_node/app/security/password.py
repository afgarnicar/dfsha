"""Hashing y verificación de contraseñas con Argon2.

Aísla la librería de hashing del resto del sistema: el resto del código solo
usa ``hash_password`` y ``verify_password`` sin conocer los detalles de Argon2.
Las contraseñas nunca se almacenan en texto plano.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

# Instancia única del hasher con los parámetros por defecto de Argon2, que son
# seguros para uso general.
_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    """Devuelve el hash Argon2 de una contraseña en texto plano."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Indica si la contraseña en texto plano corresponde al hash dado.

    Devuelve False ante cualquier discrepancia o hash inválido, sin lanzar
    excepciones hacia el llamador.
    """
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError):
        return False
