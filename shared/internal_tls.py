"""HTTPS entre el ControlNode y los DataNodes (comunicación interna).

Todo el tráfico interno (subir, pedir, borrar y replicar bloques, health checks)
viaja por HTTPS. Para no tener que crear, copiar y renovar certificados a mano
en cada VM, el certificado interno se DERIVA de la clave interna compartida
(``INTERNAL_API_KEY``), que ya está en el ``.env`` de todos los nodos:

1. Con HKDF-SHA256 se obtiene, a partir de la clave interna, una semilla de 32
   bytes y con ella una llave privada Ed25519.
2. Con esa llave se arma un certificado autofirmado con datos fijos. Como Ed25519
   firma de forma determinística, todos los nodos obtienen exactamente el mismo
   certificado.
3. Cada DataNode sirve HTTPS con ese certificado.
4. El ControlNode (y un DataNode cuando replica hacia otro) solo confía en ese
   certificado. Un servidor que no conozca la clave interna no puede presentarlo,
   así que no puede hacerse pasar por un DataNode.

No se valida el nombre del host (los DataNodes se identifican por IP privada y
todos comparten el certificado); la identidad la da la llave derivada.

Con ``INTERNAL_TLS=false`` se vuelve a HTTP plano (solo para desarrollo local).
"""

import datetime
import hashlib
import os
import ssl
import tempfile
from functools import lru_cache
from pathlib import Path

_HKDF_SALT = b"dfsha-internal-tls-v1"
_COMMON_NAME = "dfsha-internal"


def internal_tls_enabled() -> bool:
    """Indica si la comunicación interna usa HTTPS (por defecto, sí)."""
    return os.getenv("INTERNAL_TLS", "true").strip().lower() not in ("0", "false", "no")


def internal_scheme() -> str:
    """``https`` o ``http``, según ``INTERNAL_TLS``."""
    return "https" if internal_tls_enabled() else "http"


def datanode_url(host: str, port: int, path: str) -> str:
    """URL de un endpoint de un DataNode, por ejemplo ``/blocks``."""
    return f"{internal_scheme()}://{host}:{port}{path}"


@lru_cache
def internal_certificate(internal_api_key: str) -> tuple[bytes, bytes]:
    """Certificado y llave privada (PEM) derivados de la clave interna."""
    # Importación diferida: solo se necesita cryptography si se usa TLS.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.x509.oid import NameOID

    seed = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=b"ed25519"
    ).derive(internal_api_key.encode("utf-8"))
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _COMMON_NAME)])
    serial = int.from_bytes(hashlib.sha256(seed).digest()[:16], "big") >> 1
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc))
        .not_valid_after(datetime.datetime(2099, 12, 31, tzinfo=datetime.timezone.utc))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, None)
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def write_server_files(internal_api_key: str) -> tuple[str, str]:
    """Escribe el certificado y la llave en una carpeta privada temporal.

    Devuelve ``(certfile, keyfile)`` para pasárselos a uvicorn. La llave queda
    con permisos 600 y nunca se guarda junto a los bloques.
    """
    cert_pem, key_pem = internal_certificate(internal_api_key)
    directory = Path(tempfile.mkdtemp(prefix="dfsha-tls-"))
    certfile, keyfile = directory / "internal.crt", directory / "internal.key"
    certfile.write_bytes(cert_pem)
    keyfile.touch(mode=0o600)
    keyfile.write_bytes(key_pem)
    return str(certfile), str(keyfile)


@lru_cache
def _client_context(internal_api_key: str) -> ssl.SSLContext:
    cert_pem, _ = internal_certificate(internal_api_key)
    context = ssl.create_default_context(cadata=cert_pem.decode("ascii"))
    context.check_hostname = False  # la identidad la da el certificado derivado
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def client_verify(internal_api_key: str) -> ssl.SSLContext | bool:
    """Valor para el parámetro ``verify`` de httpx en llamadas internas."""
    if not internal_tls_enabled():
        return True  # HTTP plano: verify no aplica
    return _client_context(internal_api_key)
