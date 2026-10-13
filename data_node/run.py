"""Arranque del DataNode.

Sirve la API por HTTPS con el certificado interno derivado de la clave interna
(ver ``shared/internal_tls.py``). Así el ControlNode y los otros DataNodes
hablan con este nodo cifrado y pueden comprobar que es un nodo legítimo.

Variables de entorno:

- ``PORT`` (8000 por defecto) y ``HOST`` (0.0.0.0 por defecto).
- ``INTERNAL_TLS=false`` para servir HTTP plano (solo desarrollo local).
- ``RELOAD=true`` para recargar al cambiar el código (solo desarrollo local).
"""

import os

import uvicorn

from app.config import get_settings
from shared.internal_tls import internal_tls_enabled, write_server_files


def main() -> None:
    settings = get_settings()
    options: dict = {}
    if internal_tls_enabled():
        certfile, keyfile = write_server_files(settings.internal_api_key)
        options = {"ssl_certfile": certfile, "ssl_keyfile": keyfile}

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").lower() == "true",
        **options,
    )


if __name__ == "__main__":
    main()
