"""Autenticación interna del DataNode.

Los endpoints de bloques solo deben ser usados por el ControlNode y por otros
DataNodes autorizados, nunca por clientes finales. Para ello se exige una clave
compartida en el encabezado interno; es distinta del JWT de los usuarios.
"""

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings

# Nombre del encabezado HTTP con la credencial interna entre nodos. Debe
# coincidir con el que envía el ControlNode (shared/constants.py).
INTERNAL_API_KEY_HEADER = "X-DFSHA-API-Key"


def require_internal_api_key(
    x_dfsha_api_key: str | None = Header(default=None, alias=INTERNAL_API_KEY_HEADER),
) -> None:
    """Valida la clave interna del encabezado y rechaza la petición si no coincide.

    Usa una comparación en tiempo constante para no filtrar información sobre la
    clave a través del tiempo de respuesta.
    """
    expected = get_settings().internal_api_key
    if x_dfsha_api_key is None or not secrets.compare_digest(
        x_dfsha_api_key, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial interna inválida o ausente.",
        )
