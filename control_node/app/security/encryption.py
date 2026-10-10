"""Cifrado de bloques en reposo.

El ControlNode cifra cada bloque antes de enviarlo a los DataNodes, de modo que
los DataNodes solo almacenan contenido cifrado y nunca ven los datos en claro.
Se usa Fernet (AES autenticado) con una clave simétrica única del sistema,
leída de la configuración.

El checksum SHA-256 que acompaña a cada bloque se calcula sobre el contenido ya
cifrado, que es exactamente lo que el DataNode almacena y verifica.
"""

from cryptography.fernet import Fernet

from app.config import get_settings


class BlockCipher:
    """Cifra y descifra el contenido de los bloques con una clave del sistema."""

    def __init__(self, key: str | None = None) -> None:
        # Si no se pasa clave, se toma la de la configuración del sistema.
        fernet_key = key if key is not None else get_settings().encryption_key
        self._fernet = Fernet(fernet_key.encode())

    def encrypt(self, plaintext: bytes) -> bytes:
        """Devuelve el contenido cifrado de un bloque."""
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Devuelve el contenido original de un bloque cifrado."""
        return self._fernet.decrypt(ciphertext)
