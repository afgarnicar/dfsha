"""Datos comunes para hablar con los DataNodes por HTTPS.

Centraliza el esquema de las URLs internas y la verificación del certificado
interno (ver ``shared/internal_tls.py``), para que todos los clientes del
ControlNode hacia los DataNodes se comporten igual.
"""

import ssl

from app.config import get_settings
from shared.internal_tls import client_verify, datanode_url


def url(host: str, port: int, path: str) -> str:
    """URL de un endpoint de un DataNode (https por defecto)."""
    return datanode_url(host, port, path)


def verify() -> ssl.SSLContext | bool:
    """Valor de ``verify`` para httpx: solo se confía en el certificado interno."""
    return client_verify(get_settings().internal_api_key)
